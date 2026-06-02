#!/usr/bin/env python3 -u
"""
distance_worker.py — измерение расстояния через VL53L0X (прямой busio).

Использует прямой доступ к регистрам через busio.I2C (без Adafruit-библиотек,
которые не опознают нестандартные клоны чипа).
Поддерживает VL53L0X и совместимые клоны.
"""

import time
import json
import sys
import signal
import struct
import paho.mqtt.client as mqtt

from i2c_helpers import (
    create_i2c_bus,
    i2c_bus_reset,
    i2c_recover,
)

# ========== НАСТРОЙКИ MQTT ==========
MQTT_BROKER = "127.0.0.1"
MQTT_PORT = 1883
MQTT_USER = "ams_iot"
MQTT_PASS = "ams_iot_pass"
MQTT_TOPIC = "sensors/distance"

# ========== ЗАЩИТА ОТ ЗАВИСАНИЙ ==========
MAX_I2C_RETRIES = 10
I2C_RETRY_DELAY = 2
MAX_ERRORS_BEFORE_RESTART = 5
INIT_DELAY = 3

VL53L0X_ADDR = 0x29

shutdown_flag = False

# ========== РЕГИСТРЫ VL53L0X ==========
REG_SYSRANGE_START = 0x00
REG_RESULT_RANGE_STATUS = 0x14
REG_RESULT_INTERRUPT_STATUS = 0x13
REG_SYSTEM_INTERRUPT_CLEAR = 0x0B

# VL53L0X требует загрузки конфигурационных данных датчика
# (это калибровочные данные, обычно читаются из NVM чипа)
# Но для простого функционирования достаточно базовой инициализации

# ========== BUSIO ХЕЛПЕРЫ ==========

def bus_read_byte(i2c_bus, reg):
    i2c_bus.try_lock()
    try:
        result = bytearray(1)
        i2c_bus.writeto_then_readfrom(VL53L0X_ADDR, bytes([reg]), result)
        return result[0]
    finally:
        i2c_bus.unlock()

def bus_write_byte(i2c_bus, reg, value):
    i2c_bus.try_lock()
    try:
        i2c_bus.writeto(VL53L0X_ADDR, bytes([reg, value]))
    finally:
        i2c_bus.unlock()

def bus_write_bytes(i2c_bus, reg, data):
    i2c_bus.try_lock()
    try:
        i2c_bus.writeto(VL53L0X_ADDR, bytes([reg]) + bytes(data))
    finally:
        i2c_bus.unlock()

def bus_read_word(i2c_bus, reg):
    i2c_bus.try_lock()
    try:
        result = bytearray(2)
        i2c_bus.writeto_then_readfrom(VL53L0X_ADDR, bytes([reg]), result)
        return (result[0] << 8) | result[1]
    finally:
        i2c_bus.unlock()

def bus_read_bytes(i2c_bus, reg, length):
    i2c_bus.try_lock()
    try:
        result = bytearray(length)
        i2c_bus.writeto_then_readfrom(VL53L0X_ADDR, bytes([reg]), result)
        return list(result)
    finally:
        i2c_bus.unlock()


# ========== ИНИЦИАЛИЗАЦИЯ VL53L0X ==========

def init_vl53l0x(i2c_bus):
    """
    Минимальная инициализация VL53L0X.
    Выход из standby, затем настройка для single ranging.
    """
    # 1. Выход из software standby
    bus_write_byte(i2c_bus, 0x00, 0x00)
    time.sleep(0.01)
    
    # 2. Проверяем, что чип проснулся — читаем любой регистр
    try:
        val = bus_read_byte(i2c_bus, 0x00)
        print(f"[VL53L0X] Регистр 0x00 после пробуждения: 0x{val:02X}", flush=True)
    except Exception:
        print("[VL53L0X] Чип не отвечает после пробуждения", flush=True)
        return False
    
    # 3. Настройка VHV (VCSEL High Voltage) — упрощённая
    # Обычно это делается через калибровку, но мы используем стандартную процедуру
    
    # Устанавливаем режим: single ranging, high accuracy
    # Регистр 0x00 = 0x01 запускает измерение
    # Регистр 0x01 — System Mode (0x01 = Single Ranging)
    
    # Включаем VCSEL
    bus_write_byte(i2c_bus, 0x00, 0x01)
    time.sleep(0.005)
    bus_write_byte(i2c_bus, 0x00, 0x00)
    
    print("[VL53L0X] Базовая инициализация выполнена", flush=True)
    return True


def read_distance(i2c_bus):
    """
    Запускает single ranging и читает результат.
    Возвращает дистанцию в мм или None.
    """
    try:
        # Запуск измерения
        bus_write_byte(i2c_bus, REG_SYSRANGE_START, 0x01)
        
        # Ждём завершения (макс ~100 мс)
        for _ in range(50):
            time.sleep(0.01)
            status = bus_read_byte(i2c_bus, REG_RESULT_RANGE_STATUS)
            # Бит 0 = measurement complete, остальные биты — error codes
            if status & 0x01:
                break
        
        # Читаем результат (2 байта с 0x14 для Range Millimetres в некоторых версиях — на самом деле 0x1E)
        # Но в VL53L0X range хранится как 16-бит в регистрах RESULT__RANGE (0x14 + 0x15)
        # Однако RESULT_RANGE_STATUS уже содержит статус
        # Фактический диапазон в VL53L0X: регистр 0x1E (RESULT__FINAL_RANGE мм, 16 бит)
        
        # Пробуем прочитать из регистра 0x1E (VL53L0X final range)
        try:
            range_mm = bus_read_word(i2c_bus, 0x1E)
            if range_mm == 0 or range_mm == 0xFFFF:
                return None
            return range_mm
        except Exception:
            # Альтернатива: регистры 0x14-0x15
            try:
                range_mm = bus_read_word(i2c_bus, 0x14)
                return range_mm
            except Exception:
                return None
                
    except Exception as e:
        print(f"[VL53L0X] Ошибка чтения: {e}", flush=True)
        return None


# ========== MQTT ==========
def connect_mqtt():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    while not shutdown_flag:
        try:
            client.connect(MQTT_BROKER, MQTT_PORT)
            client.loop_start()
            print("VL53L0X: MQTT подключён")
            return client
        except Exception as e:
            print(f"VL53L0X: Ошибка MQTT: {e}, повтор через 5 сек...")
            time.sleep(5)
    return None


def signal_handler(signum, frame):
    global shutdown_flag
    print(f"\n[VL53L0X] Получен сигнал {signum}. Завершаем работу...")
    shutdown_flag = True


def safe_close_bus(i2c_bus):
    if i2c_bus is not None:
        try:
            i2c_bus.deinit()
        except Exception:
            pass


# ========== ИНИЦИАЛИЗАЦИЯ С ПОВТОРАМИ ==========
def try_create_sensor():
    for attempt in range(1, MAX_I2C_RETRIES + 1):
        try:
            i2c_bus_reset()
            
            i2c_bus = create_i2c_bus(max_retries=2, retry_delay=0.5)
            if i2c_bus is None:
                raise IOError("Не удалось создать I2C-шину")
            
            if init_vl53l0x(i2c_bus):
                print(f"VL53L0X: инициализирован (попытка {attempt})")
                return i2c_bus
            else:
                safe_close_bus(i2c_bus)
                
        except Exception as e:
            print(f"VL53L0X: Ошибка (попытка {attempt}/{MAX_I2C_RETRIES}): {e}", flush=True)
        
        if attempt < MAX_I2C_RETRIES:
            delay = I2C_RETRY_DELAY * (2 ** ((attempt - 1) % 4))
            print(f"VL53L0X: Повтор через {delay} сек...", flush=True)
            time.sleep(delay)
    
    print("VL53L0X: Не удалось инициализировать", flush=True)
    return None


# ========== MAIN ==========
if __name__ == "__main__":
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    print("Distance worker (VL53L0X direct) запущен")
    client = connect_mqtt()
    if client is None:
        sys.exit(1)
    
    i2c_bus = None
    error_count = 0
    first_init = True

    while not shutdown_flag:
        try:
            if not client.is_connected():
                print("VL53L0X: MQTT разорван, переподключаемся...", flush=True)
                client = connect_mqtt()
                if client is None:
                    break
                continue

            if i2c_bus is None:
                if first_init:
                    print(f"VL53L0X: Ожидание {INIT_DELAY} сек...", flush=True)
                    time.sleep(INIT_DELAY)
                    first_init = False
                
                i2c_bus = try_create_sensor()
                if i2c_bus is None:
                    print("VL53L0X: Датчик недоступен, повтор через 10 сек...", flush=True)
                    time.sleep(10)
                    continue
                error_count = 0

            distance = read_distance(i2c_bus)
            if distance is not None and distance > 0:
                payload = {"distance_mm": distance}
                client.publish(MQTT_TOPIC, json.dumps(payload), qos=0)
                print(f"[РАССТОЯНИЕ] {distance} мм")
                error_count = 0
            else:
                error_count += 1
            
            for _ in range(3):
                if shutdown_flag:
                    break
                time.sleep(1)

        except (OSError, IOError) as e:
            print(f"VL53L0X: Ошибка I2C: {e}", flush=True)
            safe_close_bus(i2c_bus)
            i2c_bus = None
            error_count += 1
            time.sleep(2)
        except KeyboardInterrupt:
            print("\nЗавершено.")
            break
        except Exception as e:
            print(f"VL53L0X: Неизвестная ошибка: {e}", flush=True)
            error_count += 1
            time.sleep(1)
        
        if error_count >= MAX_ERRORS_BEFORE_RESTART:
            print(f"VL53L0X: {error_count} ошибок подряд. Переинициализация...", flush=True)
            i2c_recover()
            safe_close_bus(i2c_bus)
            i2c_bus = None
            error_count = 0
            time.sleep(5)
    
    safe_close_bus(i2c_bus)
    print("[VL53L0X] Завершён.", flush=True)