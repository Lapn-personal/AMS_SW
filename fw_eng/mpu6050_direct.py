#!/usr/bin/env python3 -u
import time
import math
import json
import os
import sys
import paho.mqtt.client as mqtt
from smbus2 import SMBus

ADDRESS = 0x68
DELAY = 3
MQTT_BROKER = "127.0.0.1"
MQTT_PORT = 1883
MQTT_USER = "ams_iot"
MQTT_PASS = "ams_iot_pass"
MQTT_TOPIC_TILT = "sensors/tilt"

# ========== ФУНКЦИЯ ЗАГРУЗКИ СМЕЩЕНИЯ ==========
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

# ========== ОСТАЛЬНЫЕ ФУНКЦИИ (init_sensor, read_word_signed, read_sensor_data, compute_tilt) ==========
def init_sensor(bus):
    try:
        bus.write_byte_data(ADDRESS, 0x6B, 0x00)
        time.sleep(0.1)
        return True
    except Exception as e:
        print(f"Ошибка инициализации MPU6050: {e}", flush=True)
        return False

def read_word_signed(bus, reg):
    high = bus.read_byte_data(ADDRESS, reg)
    low = bus.read_byte_data(ADDRESS, reg + 1)
    val = (high << 8) | low
    if val > 32767:
        val -= 65536
    return val

def read_sensor_data(bus):
    ax = read_word_signed(bus, 0x3B) / 16384.0 * 9.81
    ay = read_word_signed(bus, 0x3D) / 16384.0 * 9.81
    az = read_word_signed(bus, 0x3F) / 16384.0 * 9.81
    gx = read_word_signed(bus, 0x43) / 131.0
    gy = read_word_signed(bus, 0x45) / 131.0
    gz = read_word_signed(bus, 0x47) / 131.0
    temp_raw = read_word_signed(bus, 0x41)
    temp = temp_raw / 340.0 + 36.53
    return (ax, ay, az, gx, gy, gz, temp)

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
client = mqtt.Client()
client.username_pw_set(MQTT_USER, MQTT_PASS)
while True:
    try:
        client.connect(MQTT_BROKER, MQTT_PORT)
        client.loop_start()
        print("MPU6050: MQTT подключён", flush=True)
        break
    except Exception as e:
        print(f"MPU6050: MQTT ошибка {e}, повтор через 3 сек", flush=True)
        time.sleep(3)

# ========== ОСНОВНОЙ ЦИКЛ ==========
bus = None
print("MPU6050 (калибруемый наклон). Ctrl+C для выхода.\n", flush=True)

while True:
    try:
        if bus is None:
            bus = SMBus(1)
            if not init_sensor(bus):
                bus = None
                time.sleep(2)
                continue

        ax, ay, az, gx, gy, gz, temp = read_sensor_data(bus)
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

        time.sleep(DELAY)

    except (OSError, IOError) as e:
        print(f"Ошибка I2C: {e}. Переподключение...", flush=True)
        if bus is not None:
            try:
                bus.close()
            except:
                pass
            bus = None
        time.sleep(2)
    except KeyboardInterrupt:
        print("\nЗавершено.")
        sys.exit(0)
    except Exception as e:
        print(f"Неизвестная ошибка: {e}", flush=True)
        time.sleep(1)