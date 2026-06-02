#!/usr/bin/env python3 -u
"""
mpu6050_direct.py — измерение наклона через MPU6050/MPU6500/ICM-20602.

Использует прямой доступ к регистрам через busio.I2C (без adafruit_mpu6050,
которая жёстко проверяет WHO_AM_I == 0x68 и не работает с MPU6500).
Проверка WHO_AM_I выполняется через i2cget (принимает 0x68 и 0x70).
"""

import time
import math
import json
import os
import sys
import signal
import subprocess
import paho.mqtt.client as mqtt

# Импортируем I2C-хелперы для стабильности шины
from i2c_helpers import (
    create_i2c_bus,
    i2c_bus_reset,
    i2c_recover,
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
    Проверяет WHO_AM_I регистр MPU6050/MPU6500 через i2cget.
    Ожидаемые значения: 0x68 (MPU6050) или 0x70 (MPU6500/ICM-20602).
    Возвращает True, если значение совпадает.
    """
    try:
        result = subprocess.run(
            ["i2cget", "-y", "1", f"0x{ADDRESS:02X}", "0x75"],
            capture_output=True, text=True, timeout=3
        )
        if result.returncode == 0:
            val = int(result.stdout.strip(), 16)
            if val in (0x68, 0x70):  # MPU6050 или MPU6500
                return True
            else:
                print(f"MPU6050: WHO_AM_I = 0x{val:02X} (ожидалось 0x68 или 0x70)", flush=True)
                return False
        else:
            return False
    except Exception as e:
        print(f"MPU6050: Ошибка проверки WHO_AM_I: {e}", flush=True)
        return False


# ========== ПРЯМОЙ ДОСТУП К РЕГИСТРАМ ЧЕРЕЗ BUSIO ==========

def bus_read_byte(i2c_bus, reg):
    """Читает один байт из регистра MPU через busio."""
    i2c_bus.try_lock()
    try:
        result = bytearray(1)
        i2c_bus.writeto_then_readfrom(ADDRESS, bytes([reg]), result)
        return result[0]
    finally:
        i2c_bus.unlock()


def bus_write_byte(i2c_bus, reg, value):
    """Записывает один байт в регистр MPU через busio."""
    i2c_bus.try_lock()
    try:
        i2c_bus.writeto(ADDRESS, bytes([reg, value]))
    finally:
        i2c_bus.unlock()


def bus_read_word_signed(i2c_bus, reg):
    """Читает 16-битное знаковое значение из пары регистров (big-endian)."""
    i2c_bus.try_lock()
    try:
        result = bytearray(2)
        i2c_bus.writeto_then_readfrom(ADDRESS, bytes([reg]), result)
        val = (result[0] << 8) | result[1]
        if val > 32767:
            val -= 65536
        return val
    finally:
        i2c_bus.unlock()


def init_sensor(i2c_bus):
    """Инициализирует MPU: выход из sleep, сброс."""
    try:
        bus_write_byte(i2c_bus, 0x6B, 0x00)
        time.sleep(0.1)
        return True
    except Exception as e:
        print(f"Ошибка инициализации MPU: {e}", flush=True)
        return False


def read_sensor_data(i2c_bus):
    """Читает все данные с MPU: акселерометр, гироскоп, температура."""
    ax = bus_read_word_signed(i2c_bus, 0x3B) / 16384.0 * 9.81
    ay = bus_read_word_signed(i2c_bus, 0x3D) / 16384.0 * 9.81
    az = bus_read_word_signed(i2c_bus, 0x3F) / 16384.0 * 9.81
    gx = bus_read_word_signed(i2c_bus, 0x43) / 131.0
    gy = bus_read_word_signed(i2c_bus, 0x45) / 131.0
    gz = bus_read_word_signed(i2c_bus, 0x47) / 131.0
    temp_raw = bus_read_word_signed(i2c_bus, 0x41)
    temp = temp_raw / 340.0 + 36.53
    return (ax, ay, az, gx, gy, gz, temp)


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


def safe_close_bus(i2c_bus):
    """Безопасно освобождает шину I2C."""
    if i2c_bus is not None:
        try:
            i2c_bus.deinit()
        except Exception:
            pass


# ========== ИНИЦИАЛИЗАЦИЯ С ПОВТОРАМИ ==========
def try_create_sensor():
    """
    Пытается создать шину busio и инициализировать MPU с повторными попытками.
    Возвращает (i2c_bus, True) при успехе или (None, False).
    """
    for attempt in range(1, MAX_I2C_RETRIES + 1):
        try:
            # Встряска шины
            i2c_bus_reset()

            # Проверяем WHO_AM_I через i2cget (стабильнее, чем busio)
            if not check_who_am_i():
                print(f"MPU6050: WHO_AM_I не совпадает (попытка {attempt}/{MAX_I2C_RETRIES})", flush=True)
                if attempt < MAX_I2C_RETRIES:
                    delay = I2C_RETRY_DELAY * (2 ** ((attempt - 1) % 4))
                    print(f"MPU6050: Повтор через {delay} сек...", flush=True)
                    time.sleep(delay)
                continue

            # Создаём шину busio
            i2c_bus = create_i2c_bus(max_retries=2, retry_delay=0.5)
            if i2c_bus is None:
                raise IOError("Не удалось создать I2C-шину")

            if init_sensor(i2c_bus):
                print(f"MPU6050: инициализирован (попытка {attempt})", flush=True)
                return i2c_bus
            else:
                safe_close_bus(i2c_bus)

        except Exception as e:
            print(f"MPU6050: Ошибка инициализации (попытка {attempt}/{MAX_I2C_RETRIES}): {e}", flush=True)

        if attempt < MAX_I2C_RETRIES:
            delay = I2C_RETRY_DELAY * (2 ** ((attempt - 1) % 4))
            print(f"MPU6050: Повтор через {delay} сек...", flush=True)
            time.sleep(delay)

    print("MPU6050: Не удалось инициализировать после всех попыток", flush=True)
    return None


# ========== MAIN ==========
if __name__ == "__main__":
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    client = connect_mqtt()
    if client is None:
        sys.exit(1)

    i2c_bus = None
    error_count = 0
    first_init = True
    print("MPU6050/MPU6500 (калибруемый наклон). Ctrl+C для выхода.\n", flush=True)

    while not shutdown_flag:
        try:
            # Проверка MQTT
            if not client.is_connected():
                print("MPU6050: MQTT разорван, переподключаемся...", flush=True)
                client = connect_mqtt()
                if client is None:
                    break
                continue

            if i2c_bus is None:
                # Задержка перед первой инициализацией
                if first_init:
                    print(f"MPU6050: Ожидание {INIT_DELAY} сек перед первой инициализацией...", flush=True)
                    time.sleep(INIT_DELAY)
                    first_init = False

                i2c_bus = try_create_sensor()
                if i2c_bus is None:
                    print("MPU6050: Датчик недоступен, повтор через 10 сек...", flush=True)
                    time.sleep(10)
                    continue
                error_count = 0

            ax, ay, az, gx, gy, gz, temp = read_sensor_data(i2c_bus)
            tilt_raw = compute_tilt(ax, ay, az)

            # Загружаем смещение и применяем его
            offset = load_tilt_offset()
            tilt_calibrated = tilt_raw - offset

            # Публикуем скорректированный угол
            payload = json.dumps({"tilt_degrees": round(tilt_calibrated, 2)})
            client.publish(MQTT_TOPIC_TILT, payload, qos=0)

            # Отладочный вывод
            print(f"Accel: X={ax:.2f}, Y={ay:.2f}, Z={az:.2f} m/s²")
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
            safe_close_bus(i2c_bus)
            i2c_bus = None
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
            safe_close_bus(i2c_bus)
            i2c_bus = None
            error_count = 0
            time.sleep(5)

    safe_close_bus(i2c_bus)
    print("[MPU6050] Завершён.", flush=True)