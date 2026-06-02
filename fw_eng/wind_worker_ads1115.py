#!/usr/bin/env python3 -u
"""
wind_worker_ads1115.py — измерение скорости ветра через ADS1115.

Использует adafruit_ads1x15 + общую shared I2C-шину (синхронизация
через try_lock/unlock из i2c_helpers).
"""

import time
import json
import sys
import signal
import paho.mqtt.client as mqtt
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn

from i2c_helpers import (
    get_shared_i2c_bus,
    release_shared_i2c_bus,
    i2c_recover,
    wake_device,
)

# ========== НАСТРОЙКИ MQTT ==========
MQTT_BROKER = "127.0.0.1"
MQTT_PORT = 1883
MQTT_USER = "ams_iot"
MQTT_PASS = "ams_iot_pass"
MQTT_TOPIC_WIND = "sensors/wind"

# ========== АДРЕС ADS1115 ==========
ADS1115_ADDR = 0x48

# ========== КАЛИБРОВКА ДАТЧИКА ВЕТРА ==========
VOLTAGE_AT_ZERO_WIND = 0.02
VOLTAGE_AT_MAX_WIND = 0.8
MAX_WIND_SPEED = 30.0

# ========== ЗАЩИТА ОТ ЗАВИСАНИЙ ==========
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
    """Инициализирует ADS1115 на SHARED I2C bus."""
    last_error = None
    for attempt in range(1, MAX_I2C_RETRIES + 1):
        try:
            # Точечно будим ADS1115 (i2cget, не трогает другие чипы)
            if attempt == 1:
                wake_device(ADS1115_ADDR)
            time.sleep(0.3)

            i2c_bus = get_shared_i2c_bus(max_retries=3, retry_delay=0.5)
            if i2c_bus is None:
                raise IOError("Shared I2C bus недоступен")

            ads = ADS.ADS1115(i2c_bus, address=ADS1115_ADDR)
            channel = AnalogIn(ads, 0)

            print(f"ADS1115: инициализирован (попытка {attempt})")
            return ads, channel

        except Exception as e:
            last_error = e
            print(f"ADS1115: Ошибка инициализации (попытка {attempt}/{MAX_I2C_RETRIES}): {e}")
            if attempt < MAX_I2C_RETRIES:
                delay = I2C_RETRY_DELAY * (2 ** (attempt - 1))
                print(f"ADS1115: Повтор через {delay} сек...")
                time.sleep(delay)

    print(f"ADS1115: Не удалось после {MAX_I2C_RETRIES} попыток: {last_error}")
    return None, None


def read_voltage(channel):
    try:
        return channel.voltage
    except Exception as e:
        print(f"ADS1115: Ошибка чтения: {e}")
        return None


def signal_handler(signum, frame):
    global shutdown_flag
    print(f"\n[ADS1115] Сигнал {signum}. Завершение...")
    shutdown_flag = True


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    print("Wind worker (ADS1115) запущен")
    client = connect_mqtt()
    if client is None:
        release_shared_i2c_bus()
        sys.exit(1)

    ads = None
    channel = None
    error_count = 0

    while not shutdown_flag:
        try:
            if not client.is_connected():
                print("ADS1115: MQTT разорван, переподключаемся...")
                client = connect_mqtt()
                if client is None:
                    break
                continue

            if ads is None or channel is None:
                ads, channel = init_sensor()
                if ads is None:
                    print("ADS1115: Датчик недоступен, повтор через 10 сек...")
                    time.sleep(10)
                    continue
                error_count = 0

            voltage = read_voltage(channel)
            if voltage is None:
                raise IOError("Ошибка чтения ADS1115")

            wind_speed = voltage_to_wind_speed(voltage)
            payload = {"wind_speed_mps": round(wind_speed, 1)}
            client.publish(MQTT_TOPIC_WIND, json.dumps(payload), qos=0)
            print(f"[ВЕТЕР ADS1115] {voltage:.4f} В -> {wind_speed:.1f} м/с")

            error_count = 0

            for _ in range(3):
                if shutdown_flag:
                    break
                time.sleep(1)

        except Exception as e:
            print(f"ADS1115: Ошибка: {e}")
            ads = None
            channel = None
            error_count += 1
            time.sleep(3)

        if error_count >= MAX_ERRORS_BEFORE_RESTART:
            print(f"ADS1115: {error_count} ошибок подряд. Переинициализация...")
            i2c_recover()
            ads = None
            channel = None
            error_count = 0
            time.sleep(5)

    release_shared_i2c_bus()
    print("[ADS1115] Завершён.")