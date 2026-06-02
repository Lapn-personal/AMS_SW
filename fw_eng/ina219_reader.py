#!/usr/bin/env python3 -u
"""
ina219_reader.py — измерение напряжения с аналогового датчика ветра через INA219.

Использует adafruit_ina219 + общую shared I2C-шину.
"""

import time
import json
import sys
import signal
import paho.mqtt.client as mqtt
import adafruit_ina219

from i2c_helpers import (
    get_shared_i2c_bus,
    release_shared_i2c_bus,
    i2c_bus_reset,
    i2c_recover,
)

# ========== НАСТРОЙКИ ==========
MQTT_BROKER = "127.0.0.1"
MQTT_PORT = 1883
MQTT_USER = "ams_iot"
MQTT_PASS = "ams_iot_pass"
MQTT_TOPIC_WIND = "sensors/wind"

VOLTAGE_MAX_MV = 500.0
WIND_MAX_MPS = 25.0

PUBLISH_INTERVAL = 1
MAX_SKIP_BEFORE_RESTART = 5
INIT_DELAY = 3

shutdown_flag = False


def mv_to_wind_speed(mv):
    if mv <= 0:
        return 0.0
    if mv >= VOLTAGE_MAX_MV:
        return WIND_MAX_MPS
    return (mv / VOLTAGE_MAX_MV) * WIND_MAX_MPS


def connect_mqtt():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    while not shutdown_flag:
        try:
            client.connect(MQTT_BROKER, MQTT_PORT)
            client.loop_start()
            print("INA219: MQTT подключён")
            return client
        except Exception as e:
            print(f"INA219: MQTT ошибка {e}, повтор через 5 сек...")
            time.sleep(5)
    return None


def init_ina():
    """Инициализирует INA219 на shared I2C bus."""
    for attempt in range(1, 11):
        try:
            i2c_bus_reset()
            time.sleep(0.4)

            i2c_bus = get_shared_i2c_bus(max_retries=2, retry_delay=0.5)
            if i2c_bus is None:
                raise IOError("Shared I2C bus недоступен")

            ina = adafruit_ina219.INA219(i2c_bus)
            print(f"INA219: инициализирован (попытка {attempt})")
            return ina
        except Exception as e:
            print(f"INA219: Ошибка инициализации (попытка {attempt}/10): {e}")
            if attempt < 10:
                delay = 2 * (2 ** ((attempt - 1) % 4))
                print(f"INA219: Повтор через {delay} сек...")
                time.sleep(delay)
    print("INA219: Не удалось инициализировать после 10 попыток")
    return None


def signal_handler(signum, frame):
    global shutdown_flag
    print(f"\n[INA219] Сигнал {signum}. Завершение...")
    shutdown_flag = True


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    print("Wind worker (INA219): напряжение с аналогового датчика ветра")

    client = connect_mqtt()
    if client is None:
        release_shared_i2c_bus()
        sys.exit(1)

    print(f"INA219: Ожидание {INIT_DELAY} сек...", flush=True)
    time.sleep(INIT_DELAY)

    ina = init_ina()
    if ina is None:
        release_shared_i2c_bus()
        sys.exit(1)

    error_count = 0

    while not shutdown_flag:
        try:
            if not client.is_connected():
                print("INA219: MQTT разорван...")
                client = connect_mqtt()
                if client is None:
                    break
                continue

            bus_voltage_v = ina.bus_voltage
            voltage_mv = bus_voltage_v * 1000
            if voltage_mv < 0:
                voltage_mv = 0

            wind_speed = mv_to_wind_speed(voltage_mv)
            payload = {"wind_speed_mps": round(wind_speed, 1)}
            client.publish(MQTT_TOPIC_WIND, json.dumps(payload), qos=0)
            print(f"[ВЕТЕР INA219] {voltage_mv:.0f} мВ -> {wind_speed:.1f} м/с")

            error_count = 0

            for _ in range(PUBLISH_INTERVAL):
                if shutdown_flag:
                    break
                time.sleep(1)

        except (OSError, IOError) as e:
            print(f"INA219: Ошибка I2C: {e}")
            error_count += 1
            try:
                ina = init_ina()
            except:
                pass
            time.sleep(2)
        except Exception as e:
            print(f"INA219: Неизвестная ошибка: {e}")
            error_count += 1
            time.sleep(1)

        if error_count >= MAX_SKIP_BEFORE_RESTART:
            print(f"INA219: {error_count} ошибок подряд. Переинициализация...")
            i2c_recover()
            try:
                ina = init_ina()
            except:
                pass
            error_count = 0
            time.sleep(3)

    release_shared_i2c_bus()
    print("[INA219] Завершён.")