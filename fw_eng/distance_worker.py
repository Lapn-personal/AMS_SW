#!/usr/bin/env python3 -u
"""
distance_worker.py — измерение расстояния через VL53L0X.

Использует adafruit_vl53l0x + busio.I2C для работы с датчиком,
с предварительной проверкой наличия чипа через busio.scan / i2cget.
"""

import time
import json
import sys
import signal
import subprocess
import paho.mqtt.client as mqtt
import adafruit_vl53l0x

# Импортируем I2C-хелперы для стабильности шины
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
INIT_DELAY = 3                # задержка перед первой инициализацией (сек)

VL53L0X_ADDR = 0x29
I2C_BUS = 1

shutdown_flag = False


def check_vl53l0x_present():
    """
    Проверяет наличие VL53L0X на шине через i2cget.
    ID-регистры (0xC0/0xC1) недоступны без полной загрузки прошивки чипа,
    поэтому просто проверяем, что устройство отвечает на своём адресе.
    Возвращает True, если устройство присутствует.
    """
    try:
        import board
        import busio
        # Простое сканирование адреса через busio
        i2c = busio.I2C(board.SCL, board.SDA)
        i2c.try_lock()
        try:
            devices = i2c.scan()
            return VL53L0X_ADDR in devices
        finally:
            i2c.unlock()
            i2c.deinit()
    except Exception:
        pass
    
    # Fallback: i2cget
    try:
        addr_str = f"0x{VL53L0X_ADDR:02X}"
        result = subprocess.run(
            ["i2cget", "-y", str(I2C_BUS), addr_str, "0x00"],
            capture_output=True, text=True, timeout=3
        )
        return result.returncode == 0
    except Exception:
        return False


# ========== ПОДКЛЮЧЕНИЕ К MQTT С ПОВТОРАМИ ==========
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
            print(f"VL53L0X: Ошибка подключения к MQTT: {e}, повтор через 5 сек...")
            time.sleep(5)
    return None


# ========== ИНИЦИАЛИЗАЦИЯ ДАТЧИКА ==========
def init_sensor():
    """Инициализирует VL53L0X с повторными попытками и проверкой ID-регистров."""
    last_error = None
    for attempt in range(1, MAX_I2C_RETRIES + 1):
        try:
            # Встряска шины перед каждой попыткой
            i2c_bus_reset()
            
            # Проверяем наличие чипа на шине
            if not check_vl53l0x_present():
                print(f"VL53L0X: чип не обнаружен (попытка {attempt}/{MAX_I2C_RETRIES})", flush=True)
                if attempt < MAX_I2C_RETRIES:
                    delay = I2C_RETRY_DELAY * (2 ** ((attempt - 1) % 4))
                    print(f"VL53L0X: Повтор через {delay} сек...", flush=True)
                    time.sleep(delay)
                continue
            
            # Создаём шину busio через унифицированный хелпер
            i2c_bus = create_i2c_bus(max_retries=2, retry_delay=0.5)
            if i2c_bus is None:
                raise IOError("Не удалось создать I2C-шину")

            sensor = adafruit_vl53l0x.VL53L0X(i2c_bus)
            print(f"VL53L0X: инициализирован (попытка {attempt})")
            return sensor
        except Exception as e:
            last_error = e
            print(f"VL53L0X: Ошибка инициализации (попытка {attempt}/{MAX_I2C_RETRIES}): {e}")
            if attempt < MAX_I2C_RETRIES:
                delay = I2C_RETRY_DELAY * (2 ** ((attempt - 1) % 4))
                print(f"VL53L0X: Повтор через {delay} сек...")
                time.sleep(delay)
    print(f"VL53L0X: Не удалось инициализировать после {MAX_I2C_RETRIES} попыток: {last_error}")
    return None


def signal_handler(signum, frame):
    global shutdown_flag
    print(f"\n[VL53L0X] Получен сигнал {signum}. Завершаем работу...")
    shutdown_flag = True


# ========== MAIN ==========
if __name__ == "__main__":
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    print("Distance worker (VL53L0X) запущен")
    client = connect_mqtt()
    if client is None:
        sys.exit(1)
    
    sensor = None
    error_count = 0
    first_init = True

    while not shutdown_flag:
        try:
            # Проверка MQTT
            if not client.is_connected():
                print("VL53L0X: MQTT разорван, переподключаемся...")
                client = connect_mqtt()
                if client is None:
                    break
                continue

            # Инициализация датчика, если ещё не
            if sensor is None:
                # Задержка перед первой инициализацией
                if first_init:
                    print(f"VL53L0X: Ожидание {INIT_DELAY} сек перед первой инициализацией...", flush=True)
                    time.sleep(INIT_DELAY)
                    first_init = False
                
                sensor = init_sensor()
                if sensor is None:
                    print("VL53L0X: Датчик недоступен, повтор через 10 сек...")
                    time.sleep(10)
                    continue
                error_count = 0

            range_mm = sensor.range
            if range_mm is not None and range_mm > 0:
                payload = {"distance_mm": range_mm}
                client.publish(MQTT_TOPIC, json.dumps(payload), qos=0)
                print(f"[РАССТОЯНИЕ] {range_mm} мм")
                error_count = 0
            else:
                print(f"[РАССТОЯНИЕ] Ошибка измерения: range={range_mm}")
                error_count += 1
            
            # Ожидание с проверкой shutdown_flag
            for _ in range(3):
                if shutdown_flag:
                    break
                time.sleep(1)

        except Exception as e:
            print(f"VL53L0X: Ошибка чтения/датчика: {e}")
            sensor = None  # сбросим, чтобы при следующей итерации переинициализировать
            error_count += 1
            time.sleep(3)
        
        # Если слишком много ошибок подряд — полный перезапуск I2C
        if error_count >= MAX_ERRORS_BEFORE_RESTART:
            print(f"VL53L0X: {error_count} ошибок подряд. Полная переинициализация I2C...")
            i2c_recover()
            sensor = None
            error_count = 0
            time.sleep(5)
    
    print("[VL53L0X] Завершён.")