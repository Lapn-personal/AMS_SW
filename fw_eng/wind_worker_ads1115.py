#!/usr/bin/env python3 -u
"""
wind_worker_ads1115.py — измерение скорости ветра через ADS1115.

Использует ПРЯМОЙ доступ к регистрам через busio.I2C (без adafruit_ads1x15,
которая нестабильна на Debian 13 — вызывает Errno 5). Shared I2C bus.
"""

import time
import json
import sys
import signal
import paho.mqtt.client as mqtt

from i2c_helpers import (
    get_shared_i2c_bus,
    release_shared_i2c_bus,
    i2c_recover,
)

# ========== НАСТРОЙКИ ==========
ADS1115_ADDR = 0x48
REG_CONVERSION = 0x00
REG_CONFIG = 0x01

# Конфиг: continuous, A0-A1 differential, ±4.096V, 128 SPS
# Биты: 15=0(continuous), 14-12=000(A0-A1 diff), 11-9=001(PGA ±4.096V), 8=0(cont),
#       7-5=100(128 SPS), 4=0(traditional), 3-0=0011(disable comparator)
ADS1115_CONFIG = 0x0383  # [0x03, 0x83] в big-endian

# Калибровка
VOLTAGE_AT_ZERO_WIND = 0.02
VOLTAGE_AT_MAX_WIND = 0.8
MAX_WIND_SPEED = 30.0

# MQTT
MQTT_BROKER = "127.0.0.1"
MQTT_PORT = 1883
MQTT_USER = "ams_iot"
MQTT_PASS = "ams_iot_pass"
MQTT_TOPIC_WIND = "sensors/wind"

# Защита
MAX_I2C_RETRIES = 5
I2C_RETRY_DELAY = 3
MAX_ERRORS_BEFORE_RESTART = 10

shutdown_flag = False


def voltage_to_wind_speed(voltage):
    if voltage <= VOLTAGE_AT_ZERO_WIND:
        return 0.0
    if voltage >= VOLTAGE_AT_MAX_WIND:
        return MAX_WIND_SPEED
    return (voltage - VOLTAGE_AT_ZERO_WIND) * (MAX_WIND_SPEED / (VOLTAGE_AT_MAX_WIND - VOLTAGE_AT_ZERO_WIND))


def bus_read_word(i2c_bus, reg):
    """Читает 16-бит из регистра ADS1115 (big-endian)."""
    i2c_bus.try_lock()
    try:
        result = bytearray(2)
        i2c_bus.writeto_then_readfrom(ADS1115_ADDR, bytes([reg]), result)
        return (result[0] << 8) | result[1]
    finally:
        i2c_bus.unlock()


def bus_write_word(i2c_bus, reg, value):
    """Пишет 16-бит в регистр ADS1115 (big-endian)."""
    i2c_bus.try_lock()
    try:
        i2c_bus.writeto(ADS1115_ADDR, bytes([reg, (value >> 8) & 0xFF, value & 0xFF]))
    finally:
        i2c_bus.unlock()


def connect_mqtt():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    while not shutdown_flag:
        try:
            client.connect(MQTT_BROKER, MQTT_PORT)
            client.loop_start()
            print("ADS1115: MQTT подключён")
            return client
        except Exception as e:
            print(f"ADS1115: MQTT ошибка {e}, повтор через 5 сек...")
            time.sleep(5)
    return None


def init_sensor():
    """Инициализирует ADS1115 через прямой busio."""
    last_error = None
    for attempt in range(1, MAX_I2C_RETRIES + 1):
        try:
            # Без агрессивных сбросов — просто пауза для стабилизации шины
            time.sleep(1)

            i2c_bus = get_shared_i2c_bus(max_retries=3, retry_delay=0.5)
            if i2c_bus is None:
                raise IOError("Shared I2C bus недоступен")

            # Пишем конфиг
            bus_write_word(i2c_bus, REG_CONFIG, ADS1115_CONFIG)
            time.sleep(0.01)

            # Читаем обратно для проверки
            config_read = bus_read_word(i2c_bus, REG_CONFIG)
            print(f"ADS1115: config=0x{config_read:04X} (попытка {attempt})")

            print(f"ADS1115: инициализирован (попытка {attempt})")
            return i2c_bus

        except Exception as e:
            last_error = e
            print(f"ADS1115: Ошибка инициализации (попытка {attempt}/{MAX_I2C_RETRIES}): {e}")
            # Утечка refcount: get_shared_i2c_bus был вызван, нужно освободить
            release_shared_i2c_bus()
            if attempt < MAX_I2C_RETRIES:
                delay = I2C_RETRY_DELAY * (2 ** (attempt - 1))
                print(f"ADS1115: Повтор через {delay} сек...")
                time.sleep(delay)

    print(f"ADS1115: Не удалось после {MAX_I2C_RETRIES} попыток: {last_error}")
    return None


def read_voltage(i2c_bus):
    """Читает напряжение с ADS1115 в вольтах."""
    raw = bus_read_word(i2c_bus, REG_CONVERSION)
    # ±4.096V, 16-bit signed
    if raw > 32767:
        raw -= 65536
    voltage = raw * 4.096 / 32768.0
    return voltage


def signal_handler(signum, frame):
    global shutdown_flag
    print(f"\n[ADS1115] Сигнал {signum}. Завершение...")
    shutdown_flag = True


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    print("Wind worker (ADS1115 direct) запущен")
    client = connect_mqtt()
    if client is None:
        release_shared_i2c_bus()
        sys.exit(1)

    i2c_bus = None
    error_count = 0

    while not shutdown_flag:
        try:
            if not client.is_connected():
                print("ADS1115: MQTT разорван, переподключаемся...")
                client = connect_mqtt()
                if client is None:
                    break
                continue

            if i2c_bus is None:
                i2c_bus = init_sensor()
                if i2c_bus is None:
                    print("ADS1115: Датчик недоступен, повтор через 10 сек...")
                    time.sleep(10)
                    continue
                error_count = 0

            voltage = read_voltage(i2c_bus)
            wind_speed = voltage_to_wind_speed(voltage)
            payload = {"wind_speed_mps": round(wind_speed, 1)}
            client.publish(MQTT_TOPIC_WIND, json.dumps(payload), qos=0)
            print(f"[ВЕТЕР ADS1115] {voltage:.4f} В -> {wind_speed:.1f} м/с")

            error_count = 0

            for _ in range(3):
                if shutdown_flag:
                    break
                time.sleep(1)

        except (OSError, IOError) as e:
            print(f"ADS1115: Ошибка I2C: {e}")
            release_shared_i2c_bus()
            i2c_recover()
            i2c_bus = None
            error_count += 1
            time.sleep(3)
        except Exception as e:
            print(f"ADS1115: Ошибка: {e}")
            i2c_bus = None
            error_count += 1
            time.sleep(3)

        if error_count >= MAX_ERRORS_BEFORE_RESTART:
            print(f"ADS1115: {error_count} ошибок подряд. Полная переинициализация...")
            release_shared_i2c_bus()
            i2c_recover()
            i2c_bus = None
            error_count = 0
            time.sleep(5)

    release_shared_i2c_bus()
    print("[ADS1115] Завершён.")