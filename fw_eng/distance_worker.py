#!/usr/bin/env python3 -u
"""
distance_worker.py — измерение расстояния через VL53L0X / VL53L0K.

Monkey-patch: перед импортом adafruit_vl53l0x заменяем проверку ID-регистров,
чтобы библиотека приняла любой чип (включая клоны с нестандартными ID).
Использует общую shared I2C-шину.
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

# ========== MONKEY-PATCH: отключаем проверку ID в adafruit_vl53l0x ==========
# Библиотека проверяет регистры 0xC0==0xEE и 0xC1==0xAA.
# Клоны (VL53L0K) имеют другие ID. Патчим _read_model_id перед импортом.
import adafruit_vl53l0x

_original_read_model_id = getattr(adafruit_vl53l0x.VL53L0X, '_read_model_id', None)
if _original_read_model_id is not None:
    def _patched_read_model_id(self):
        print("[VL53L0X] Monkey-patch: пропускаем проверку ID (клон VL53L0K)", flush=True)
    adafruit_vl53l0x.VL53L0X._read_model_id = _patched_read_model_id
else:
    print("[VL53L0X] Предупреждение: не удалось найти _read_model_id для патча", flush=True)

# ========== НАСТРОЙКИ ==========
MQTT_BROKER = "127.0.0.1"
MQTT_PORT = 1883
MQTT_USER = "ams_iot"
MQTT_PASS = "ams_iot_pass"
MQTT_TOPIC = "sensors/distance"

MAX_I2C_RETRIES = 10
I2C_RETRY_DELAY = 2
MAX_ERRORS_BEFORE_RESTART = 5
INIT_DELAY = 3

shutdown_flag = False


def init_sensor():
    """Инициализирует VL53L0X/VL53L0K через adafruit_vl53l0x + shared bus."""
    for attempt in range(1, MAX_I2C_RETRIES + 1):
        try:
            i2c_bus_reset()
            time.sleep(0.4)

            i2c_bus = get_shared_i2c_bus(max_retries=2, retry_delay=0.5)
            if i2c_bus is None:
                raise IOError("Shared I2C bus недоступен")

            sensor = adafruit_vl53l0x.VL53L0X(i2c_bus)
            print(f"VL53L0X: инициализирован (попытка {attempt})")
            return sensor
        except Exception as e:
            print(f"VL53L0X: Ошибка инициализации (попытка {attempt}/{MAX_I2C_RETRIES}): {e}")
            if attempt < MAX_I2C_RETRIES:
                delay = I2C_RETRY_DELAY * (2 ** ((attempt - 1) % 4))
                print(f"VL53L0X: Повтор через {delay} сек...")
                time.sleep(delay)
    print(f"VL53L0X: Не удалось инициализировать после {MAX_I2C_RETRIES} попыток")
    return None


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
            print(f"VL53L0X: MQTT ошибка {e}, повтор через 5 сек...")
            time.sleep(5)
    return None


def signal_handler(signum, frame):
    global shutdown_flag
    print(f"\n[VL53L0X] Сигнал {signum}. Завершение...")
    shutdown_flag = True


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    print("Distance worker (VL53L0X + monkey-patch for VL53L0K) запущен")
    client = connect_mqtt()
    if client is None:
        release_shared_i2c_bus()
        sys.exit(1)

    sensor = None
    error_count = 0
    first_init = True

    while not shutdown_flag:
        try:
            if not client.is_connected():
                print("VL53L0X: MQTT разорван...", flush=True)
                client = connect_mqtt()
                if client is None:
                    break
                continue

            if sensor is None:
                if first_init:
                    print(f"VL53L0X: Ожидание {INIT_DELAY} сек...", flush=True)
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

            for _ in range(3):
                if shutdown_flag:
                    break
                time.sleep(1)

        except (OSError, IOError) as e:
            print(f"VL53L0X: Ошибка I2C: {e}", flush=True)
            sensor = None
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
            sensor = None
            error_count = 0
            time.sleep(5)

    release_shared_i2c_bus()
    print("[VL53L0X] Завершён.", flush=True)