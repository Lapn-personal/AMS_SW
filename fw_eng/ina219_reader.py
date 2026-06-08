#!/usr/bin/env python3 -u
"""
ina219_reader.py — мониторинг DC-питания системы через INA219.

Измеряет напряжение на шине и ток через шунт, определяет режим питания:
  - charging:   идёт заряд резервной АКБ (внешнее питание присутствует)
  - discharging: система питается от резервной АКБ
  - external:   внешнее питание, АКБ не заряжается/не разряжается
  - battery:    низкое напряжение — вероятно, только от АКБ

Публикует в sensors/system_power.
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
    i2c_recover,
)

# ========== НАСТРОЙКИ ==========
MQTT_BROKER = "127.0.0.1"
MQTT_PORT = 1883
MQTT_USER = "ams_iot"
MQTT_PASS = "ams_iot_pass"
MQTT_TOPIC = "sensors/system_power"

# Пороги для определения режима питания (мА)
CURRENT_CHARGING_THRESHOLD_MA = 50.0     # ток > +50мА -> заряд
CURRENT_DISCHARGING_THRESHOLD_MA = -50.0  # ток < -50мА -> разряд
VOLTAGE_LOW_THRESHOLD_V = 4.5            # напряжение < 4.5В -> только АКБ

PUBLISH_INTERVAL = 3   # публикация каждые ~3 сек (3 итерации сна по 1 сек)
MAX_SKIP_BEFORE_RESTART = 5
INIT_DELAY = 3

shutdown_flag = False


def determine_power_mode(current_ma, bus_voltage_v):
    """
    Определяет режим питания по току и напряжению.
    Возвращает одну из строк: "charging", "discharging", "external", "battery".
    """
    if current_ma > CURRENT_CHARGING_THRESHOLD_MA:
        return "charging"
    elif current_ma < CURRENT_DISCHARGING_THRESHOLD_MA:
        return "discharging"
    elif bus_voltage_v < VOLTAGE_LOW_THRESHOLD_V:
        return "battery"
    else:
        return "external"


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
            time.sleep(0.5)

            i2c_bus = get_shared_i2c_bus(max_retries=2, retry_delay=0.5)
            if i2c_bus is None:
                raise IOError("Shared I2C bus недоступен")

            ina = adafruit_ina219.INA219(i2c_bus)
            # Устанавливаем 16V/400mA range для типового шунта 0.1 Ом
            # (по умолчанию adafruit_ina219 настраивает на ±3.2A с шунтом 0.1 Ом)
            print(f"INA219: инициализирован (попытка {attempt})")
            return ina
        except Exception as e:
            print(f"INA219: Ошибка инициализации (попытка {attempt}/10): {e}")
            release_shared_i2c_bus()
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

    print("Power monitor (INA219): DC-питание системы")

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

            # Читаем напряжение на шине (после шунта, т.е. напряжение нагрузки)
            bus_voltage_v = ina.bus_voltage
            if bus_voltage_v < 0:
                bus_voltage_v = 0.0

            # Читаем ток через шунт (положительный — заряд/потребление от шины,
            # отрицательный — разряд/отдача в шину)
            current_ma = ina.current  # adafruit_ina219 возвращает мА

            power_mode = determine_power_mode(current_ma, bus_voltage_v)

            payload = {
                "bus_voltage_v": round(bus_voltage_v, 3),
                "current_ma": round(current_ma, 1),
                "power_mode": power_mode,
            }
            client.publish(MQTT_TOPIC, json.dumps(payload), qos=0)
            print(
                f"[ПИТАНИЕ INA219] {bus_voltage_v:.3f} В, "
                f"{current_ma:+.1f} мА -> {power_mode}"
            )

            error_count = 0

            for _ in range(PUBLISH_INTERVAL):
                if shutdown_flag:
                    break
                time.sleep(1)

        except (OSError, IOError) as e:
            print(f"INA219: Ошибка I2C: {e}")
            error_count += 1
            release_shared_i2c_bus()
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
            release_shared_i2c_bus()
            i2c_recover()
            try:
                ina = init_ina()
            except:
                pass
            error_count = 0
            time.sleep(3)

    release_shared_i2c_bus()
    print("[INA219] Завершён.")