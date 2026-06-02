#!/usr/bin/env python3 -u
"""
distance_worker.py — измерение расстояния, автоопределение чипа на 0x29.

Сначала читает ID-регистры чипа (probe), затем инициализирует
подходящий драйвер. VL53L0K — клон VL53L0X с другими ID.
"""

import time
import json
import sys
import signal
import paho.mqtt.client as mqtt

from i2c_helpers import (
    get_shared_i2c_bus,
    release_shared_i2c_bus,
    i2c_bus_reset,
    i2c_recover,
)

MQTT_BROKER = "127.0.0.1"
MQTT_PORT = 1883
MQTT_USER = "ams_iot"
MQTT_PASS = "ams_iot_pass"
MQTT_TOPIC = "sensors/distance"

MAX_I2C_RETRIES = 10
I2C_RETRY_DELAY = 2
MAX_ERRORS_BEFORE_RESTART = 5
INIT_DELAY = 3

SENSOR_ADDR = 0x29
shutdown_flag = False


def i2c_read_reg(i2c_bus, reg_addr):
    """Читает 1 байт из 8-битного регистра VL53L0X."""
    i2c_bus.try_lock()
    try:
        result = bytearray(1)
        i2c_bus.writeto_then_readfrom(SENSOR_ADDR, bytes([reg_addr]), result)
        return result[0]
    finally:
        i2c_bus.unlock()


def i2c_write_reg(i2c_bus, reg_addr, value):
    """Пишет 1 байт в 8-битный регистр VL53L0X."""
    i2c_bus.try_lock()
    try:
        i2c_bus.writeto(SENSOR_ADDR, bytes([reg_addr, value]))
    finally:
        i2c_bus.unlock()


def i2c_read_word(i2c_bus, reg_addr):
    """Читает 16-бит из 8-битного регистра (big-endian)."""
    i2c_bus.try_lock()
    try:
        result = bytearray(2)
        i2c_bus.writeto_then_readfrom(SENSOR_ADDR, bytes([reg_addr]), result)
        return (result[0] << 8) | result[1]
    finally:
        i2c_bus.unlock()


def probe_chip(i2c_bus):
    """Читает все известные ID-регистры VL53L0X/VL53L1X/VL6180X."""
    print("[PROBE] === ID-регистры чипа на 0x29 ===", flush=True)
    
    # Waking up
    i2c_write_reg(i2c_bus, 0x00, 0x00)
    time.sleep(0.02)
    
    regs = {
        '0x00_SYSRANGE_START': 0x00,
        '0x13_RESULT_INT_STATUS': 0x13,
        '0x14_RESULT_RANGE_STATUS': 0x14,
        '0xC0_IDENTIFICATION0': 0xC0,
        '0xC1_IDENTIFICATION1': 0xC1,
        '0xC2_IDENTIFICATION2': 0xC2,
        '0x51_SIGNAL_RATE_MSB': 0x51,
        '0x91_VHV_CONFIG_PAD': 0x91,
        '0x75_WHO_AM_I_MPU': 0x75,  # not VL53 but let's check
    }
    
    for name, reg in regs.items():
        try:
            val = i2c_read_reg(i2c_bus, reg)
            print(f"  {name}: 0x{val:02X} ({val})", flush=True)
        except Exception as e:
            print(f"  {name}: ERROR ({e})", flush=True)
    
    # Также пробуем прочитать несколько регистров VL6180X
    print("[PROBE] --- VL6180X регистры (16-бит) ---", flush=True)
    try:
        val = i2c_read_reg(i2c_bus, 0x00)  # VL6180X MODEL_ID low
        print(f"  0x0000_MODEL_ID: 0x{val:02X}", flush=True)
    except:
        pass
    
    print("[PROBE] Конец дампа", flush=True)


def init_vl53l0x_direct(i2c_bus):
    """
    Прямая инициализация VL53L0X/VL53L0K через регистры.
    Адаптировано из ST API (VL53L0X_DataInit + StaticInit).
    """
    # 1. Выход из standby
    i2c_write_reg(i2c_bus, 0x00, 0x00)
    time.sleep(0.01)
    
    # 2. Сброс VHV (как в ST VL53L0X_DataInit)
    i2c_write_reg(i2c_bus, 0x88, 0x00)  # I2C_SLAVE_DEVICE_ADDRESS
    i2c_write_reg(i2c_bus, 0x80, 0x01)  # VHV_CONFIG_INIT
    i2c_write_reg(i2c_bus, 0xFF, 0x01)  # enable config
    i2c_write_reg(i2c_bus, 0x00, 0x00)
    time.sleep(0.01)
    
    # 3. Читаем VHV_CONFIG_PAD
    vhv = i2c_read_reg(i2c_bus, 0x91)
    print(f"[VL53L0X] VHV_CONFIG_PAD: 0x{vhv:02X}", flush=True)
    
    # 4. Устанавливаем 2.8V (стандартное значение)
    i2c_write_reg(i2c_bus, 0x00, 0x01)
    i2c_write_reg(i2c_bus, 0xFF, 0x00)
    i2c_write_reg(i2c_bus, 0x80, 0x00)
    
    # 5. Конфигурируем SIGMA_ESTIMATOR и VCSEL (StaticInit)
    # Адрес I2C
    i2c_write_reg(i2c_bus, 0x89, 0x01)  # I2C_STANDARD_MODE
    i2c_write_reg(i2c_bus, 0x83, 0x01)  # PRE_RANGE_CONFIG__VCSEL_PERIOD (14->18 for short range)
    
    # Signal rate limit (0.25 MCPS)
    i2c_write_reg(i2c_bus, 0x00, 0x01)
    i2c_write_reg(i2c_bus, 0x4C, 0x00)
    i2c_write_reg(i2c_bus, 0x4D, 0x00)
    i2c_write_reg(i2c_bus, 0x4E, 0x01)
    i2c_write_reg(i2c_bus, 0x4F, 0xC4)  # 0.25 * 65536
    i2c_write_reg(i2c_bus, 0x00, 0x00)
    
    # 6. Timing budget
    i2c_write_reg(i2c_bus, 0x60, 0x00)
    i2c_write_reg(i2c_bus, 0x61, 0xCC)  # 200 ms
    i2c_write_reg(i2c_bus, 0x62, 0x0B)
    i2c_write_reg(i2c_bus, 0x63, 0x00)
    
    print("[VL53L0X] Прямая инициализация завершена", flush=True)
    return True


def read_distance_vl53l0x_direct(i2c_bus):
    """Запускает single ranging и читает результат в мм."""
    try:
        # Запуск
        i2c_write_reg(i2c_bus, 0x00, 0x01)
        i2c_write_reg(i2c_bus, 0x00, 0x00)
        
        # Ждём завершения
        for _ in range(100):
            time.sleep(0.01)
            status = i2c_read_reg(i2c_bus, 0x13)
            if status & 0x07:  # device status != 0
                break
        
        # Читаем 16-бит range
        range_mm = i2c_read_word(i2c_bus, 0x14)
        # Clear interrupt
        i2c_write_reg(i2c_bus, 0x0B, 0x01)
        
        return range_mm if range_mm > 0 and range_mm < 8190 else None
    except Exception as e:
        print(f"[VL53L0X] Ошибка чтения: {e}", flush=True)
        return None


def connect_mqtt():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    while not shutdown_flag:
        try:
            client.connect(MQTT_BROKER, MQTT_PORT)
            client.loop_start()
            print("Distance: MQTT подключён")
            return client
        except Exception as e:
            print(f"Distance: MQTT ошибка {e}, повтор через 5 сек...")
            time.sleep(5)
    return None


def signal_handler(signum, frame):
    global shutdown_flag
    print(f"\n[Distance] Сигнал {signum}. Завершение...")
    shutdown_flag = True


def try_init():
    """Инициализация с probe + прямой драйвер."""
    for attempt in range(1, MAX_I2C_RETRIES + 1):
        try:
            i2c_bus_reset()
            time.sleep(0.4)

            i2c_bus = get_shared_i2c_bus(max_retries=2, retry_delay=0.5)
            if i2c_bus is None:
                raise IOError("Shared I2C bus недоступен")

            # Один раз выводим ID-регистры
            if attempt == 1:
                probe_chip(i2c_bus)
            
            if init_vl53l0x_direct(i2c_bus):
                print(f"Distance: инициализирован (попытка {attempt})")
                return i2c_bus

        except Exception as e:
            print(f"Distance: Ошибка (попытка {attempt}/{MAX_I2C_RETRIES}): {e}")

        if attempt < MAX_I2C_RETRIES:
            delay = I2C_RETRY_DELAY * (2 ** ((attempt - 1) % 4))
            print(f"Distance: Повтор через {delay} сек...")
            time.sleep(delay)

    print("Distance: Не удалось инициализировать")
    return None


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    print("Distance worker (VL53L0X direct) запущен")
    client = connect_mqtt()
    if client is None:
        release_shared_i2c_bus()
        sys.exit(1)

    i2c_bus = None
    error_count = 0
    first_init = True

    while not shutdown_flag:
        try:
            if not client.is_connected():
                print("Distance: MQTT разорван...", flush=True)
                client = connect_mqtt()
                if client is None:
                    break
                continue

            if i2c_bus is None:
                if first_init:
                    print(f"Distance: Ожидание {INIT_DELAY} сек...", flush=True)
                    time.sleep(INIT_DELAY)
                    first_init = False

                i2c_bus = try_init()
                if i2c_bus is None:
                    print("Distance: Датчик недоступен, повтор через 10 сек...")
                    time.sleep(10)
                    continue
                error_count = 0

            distance = read_distance_vl53l0x_direct(i2c_bus)
            if distance is not None:
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
            print(f"Distance: Ошибка I2C: {e}", flush=True)
            i2c_bus = None
            error_count += 1
            time.sleep(2)
        except KeyboardInterrupt:
            print("\nЗавершено.")
            break
        except Exception as e:
            print(f"Distance: Неизвестная ошибка: {e}", flush=True)
            error_count += 1
            time.sleep(1)

        if error_count >= MAX_ERRORS_BEFORE_RESTART:
            print(f"Distance: {error_count} ошибок подряд. Переинициализация...", flush=True)
            i2c_recover()
            i2c_bus = None
            error_count = 0
            time.sleep(5)

    release_shared_i2c_bus()
    print("[Distance] Завершён.", flush=True)