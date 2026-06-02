#!/usr/bin/env python3 -u
"""
mpu6050_direct.py — измерение наклона через MPU6050.

Использует adafruit_mpu6050 + busio.I2C для работы с датчиком,
с предварительной проверкой WHO_AM_I через i2cget.
"""

import time
import math
import json
import os
import sys
import signal
import subprocess
import paho.mqtt.client as mqtt
import adafruit_mpu6050

# Импортируем I2C-хелперы для стабильности шины
from i2c_helpers import (
    create_i2c_bus,
    i2c_bus_reset,
    i2c_recover,
    ensure_i2c_ready,
)

ADDRESS = 0x68
DELAY = 3
MQTT_BROKER = "127.0.0.1"
MQTT_PORT = 1883
MQTT_USER = "ams_iot"
MQTT_PASS = "ams_iot_pass"
MQTT_TOPIC_TILT = "sensors/tilt"

# ========== ЗАЩИТА ОТ ЗАВИСАНИЙ ==========
MAX_I2C_RETRIES = 10
I2C_RETRY_DELAY = 2
MAX_ERRORS_BEFORE_RESTART = 5
INIT_DELAY = 3                # задержка перед первой инициализацией (сек)

shutdown_flag = False


def check_who_am_i():
    """
    Проверяет WHO_AM_I регистр MPU6050 через i2cget.
    Ожидаемое значение: 0x68.
    Возвращает True, если значение совпадает.
    """
    try:
        result = subprocess.run(
            ["i2cget", "-y", "1", f"0x{ADDRESS:02X}", "0x75"],
            capture_output=True, text=True, timeout=3
        )
        if result.returncode == 0:
            val = int(result.stdout.strip(), 16)
            if val == 0x68:
                return True
            else:
                print(f"MPU6050: WHO_AM_I = 0x{val:02X} (ожидалось 0x68)", flush=True)
                return False
        else:
            return False
    except Exception as e:
        print(f"MPU6050: Ошибка проверки WHO_AM_I: {e}", flush=True)
        return False


def load_tilt_offset():
    """Загружает смещение из файла ~/fw_settings/tilt_calib.json, возвращает float (по умолчанию 0)."""
    settings_dir = os.path.expanduser("~/fw_settings")
    calib_file = os.path.join(settings_dir, "tilt_calib.json")
    if not os.path.exists(calib_file):
        return 0.0
    try:
        with open(calib_file, "r") as f:
            data = json.load(f)
            return data.get("offset", 0.0)
    except Exception:
        return 0.0


def init_sensor():
    """
    Инициализирует MPU6050 через adafruit_mpu6050 с повторными попытками.
    Предварительно проверяет WHO_AM_I через i2cget.
    """
    for attempt in range(1, MAX_I2C_RETRIES + 1):
        try:
            # Встряска шины перед каждой попыткой
            i2c_bus_reset()

            # Проверяем WHO_AM_I через i2cget (более стабильно, чем через busio)
            if not check_who_am_i():
                print(f"MPU6050: WHO_AM_I не совпадает (попытка {attempt}/{MAX_I2C_RETRIES})", flush=True)
                if attempt < MAX_I2C_RETRIES:
                    delay = I2C_RETRY_DELAY * (2 ** ((attempt - 1) % 4))
                    print(f"MPU6050: Повтор через {delay} сек...", flush=True)
                    time.sleep(delay)
                continue

            # Создаём шину busio и инициализируем датчик
            i2c_bus = create_i2c_bus(max_retries=2, retry_delay=0.5)
            if i2c_bus is None:
                raise IOError("Не удалось создать I2C-шину")

            sensor = adafruit_mpu6050.MPU6050(i2c_bus)
            print(f"MPU6050: инициализирован (попытка {attempt})", flush=True)
            return sensor

        except Exception as e:
            print(f"MPU6050: Ошибка инициализации (попытка {attempt}/{MAX_I2C_RETRIES}): {e}", flush=True)
            if attempt < MAX_I2C_RETRIES:
                delay = I2C_RETRY_DELAY * (2 ** ((attempt - 1) % 4))
                print(f"MPU6050: Повтор через {delay} сек...", flush=True)
                time.sleep(delay)

    print("MPU6050: Не удалось инициализировать после всех попыток", flush=True)
    return None


def compute_tilt(ax, ay, az):
    ax_g = ax / 9.81
    ay_g = ay / 9.81
    az_g = az / 9.81
    norm = math.sqrt(ax_g*ax_g + ay_g*ay_g + az_g*az_g)
    if norm < 0.001:
        return 0.0
    angle_rad = math.acos(min(1.0, max(-1.0, az_g / norm)))
    return math.degrees(angle_rad)


# ========== MQTT ПОДКЛЮЧЕНИЕ ==========
def connect_mqtt():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    while not shutdown_flag:
        try:
            client.connect(MQTT_BROKER, MQTT_PORT)
            client.loop_start()
            print("MPU6050: MQTT подключён", flush=True)
            return client
        except Exception as e:
            print(f"MPU6050: MQTT ошибка {e}, повтор через 3 сек", flush=True)
            time.sleep(3)
    return None


def signal_handler(signum, frame):
    global shutdown_flag
    print(f"\n[MPU6050] Получен сигнал {signum}. Завершаем работу...", flush=True)
    shutdown_flag = True


def safe_close_sensor(sensor):
    """Безопасно освобождает шину датчика."""
    if sensor is not None:
        try:
            sensor.i2c_device.i2c.deinit()
        except Exception:
            pass


# ========== MAIN ==========
if __name__ == "__main__":
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    client = connect_mqtt()
    if client is None:
        sys.exit(1)

    sensor = None
    error_count = 0
    first_init = True
    print("MPU6050 (калибруемый наклон). Ctrl+C для выхода.\n", flush=True)

    while not shutdown_flag:
        try:
            # Проверка MQTT
            if not client.is_connected():
                print("MPU6050: MQTT разорван, переподключаемся...", flush=True)
                client = connect_mqtt()
                if client is None:
                    break
                continue

            if sensor is None:
                # Задержка перед первой инициализацией
                if first_init:
                    print(f"MPU6050: Ожидание {INIT_DELAY} сек перед первой инициализацией...", flush=True)
                    time.sleep(INIT_DELAY)
                    first_init = False

                sensor = init_sensor()
                if sensor is None:
                    print("MPU6050: Датчик недоступен, повтор через 10 сек...", flush=True)
                    time.sleep(10)
                    continue
                error_count = 0

            # Читаем данные через adafruit_mpu6050
            acc_x, acc_y, acc_z = sensor.acceleration
            gx, gy, gz = sensor.gyro
            temp = sensor.temperature

            tilt_raw = compute_tilt(acc_x, acc_y, acc_z)

            # Загружаем смещение и применяем его
            offset = load_tilt_offset()
            tilt_calibrated = tilt_raw - offset

            # Публикуем скорректированный угол
            payload = json.dumps({"tilt_degrees": round(tilt_calibrated, 2)})
            client.publish(MQTT_TOPIC_TILT, payload, qos=0)

            # Отладочный вывод
            print(f"Accel: X={acc_x:.2f}, Y={acc_y:.2f}, Z={acc_z:.2f} m/s²")
            print(f"Gyro:  X={gx:.2f}, Y={gy:.2f}, Z={gz:.2f} °/s")
            print(f"Temp:  {temp:.2f} °C   |   Наклон: {tilt_calibrated:.2f}° (сырой: {tilt_raw:.2f}°, смещение: {offset:.2f}°)")
            print("-" * 70, flush=True)

            error_count = 0

            # Ожидание с проверкой shutdown_flag
            for _ in range(DELAY):
                if shutdown_flag:
                    break
                time.sleep(1)

        except (OSError, IOError) as e:
            print(f"Ошибка I2C: {e}. Переподключение...", flush=True)
            safe_close_sensor(sensor)
            sensor = None
            error_count += 1
            time.sleep(2)
        except KeyboardInterrupt:
            print("\nЗавершено.")
            break
        except Exception as e:
            print(f"Неизвестная ошибка: {e}", flush=True)
            error_count += 1
            time.sleep(1)
        
        # Если слишком много ошибок подряд — полный перезапуск I2C
        if error_count >= MAX_ERRORS_BEFORE_RESTART:
            print(f"MPU6050: {error_count} ошибок подряд. Полная переинициализация I2C...", flush=True)
            i2c_recover()
            safe_close_sensor(sensor)
            sensor = None
            error_count = 0
            time.sleep(5)
    
    safe_close_sensor(sensor)
    print("[MPU6050] Завершён.", flush=True)