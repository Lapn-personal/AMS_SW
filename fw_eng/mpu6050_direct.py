#!/usr/bin/env python3 -u
"""
mpu6050_direct.py — измерение наклона через MPU6050.

Использует smbus2 для работы с I2C.
Добавлена проверка WHO_AM_I через i2cget перед инициализацией
и многоуровневое восстановление шины при ошибках.
"""

import time
import math
import json
import os
import sys
import signal
import subprocess
import paho.mqtt.client as mqtt
from smbus2 import SMBus

# Импортируем I2C-хелперы для стабильности шины
from i2c_helpers import i2c_bus_reset, i2c_recover, ensure_i2c_ready

ADDRESS = 0x68
DELAY = 3
MQTT_BROKER = "127.0.0.1"
MQTT_PORT = 1883
MQTT_USER = "ams_iot"
MQTT_PASS = "ams_iot_pass"
MQTT_TOPIC_TILT = "sensors/tilt"

# ========== ЗАЩИТА ОТ ЗАВИСАНИЙ ==========
MAX_I2C_RETRIES = 10          # увеличено с 5 до 10
I2C_RETRY_DELAY = 2           # уменьшено для более частых попыток
MAX_ERRORS_BEFORE_RESTART = 5 # уменьшено — быстрее переходим к recovery
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


def close_bus(bus):
    """Безопасно закрывает SMBus."""
    if bus is not None:
        try:
            bus.close()
        except Exception:
            pass


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


# ========== MAIN ==========
if __name__ == "__main__":
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    client = connect_mqtt()
    if client is None:
        sys.exit(1)

    bus = None
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

            if bus is None:
                # Задержка перед первой инициализацией (чтобы шина успела стабилизироваться)
                if first_init:
                    print(f"MPU6050: Ожидание {INIT_DELAY} сек перед первой инициализацией...", flush=True)
                    time.sleep(INIT_DELAY)
                    first_init = False
                
                # Многоуровневая инициализация с проверкой WHO_AM_I
                initialized = False
                for attempt in range(1, MAX_I2C_RETRIES + 1):
                    # Встряска шины перед каждой попыткой
                    i2c_bus_reset()
                    
                    # Проверяем WHO_AM_I через i2cget (более стабильно, чем smbus2)
                    if check_who_am_i():
                        # Датчик отвечает — пробуем через smbus2
                        try:
                            bus = SMBus(1)
                            if init_sensor(bus):
                                print(f"MPU6050: инициализирован (попытка {attempt})", flush=True)
                                initialized = True
                                break
                            else:
                                close_bus(bus)
                                bus = None
                        except Exception as e:
                            close_bus(bus)
                            bus = None
                            print(f"MPU6050: smbus2 ошибка (попытка {attempt}): {e}", flush=True)
                    else:
                        print(f"MPU6050: WHO_AM_I не совпадает (попытка {attempt}/{MAX_I2C_RETRIES})", flush=True)
                    
                    if attempt < MAX_I2C_RETRIES:
                        delay = I2C_RETRY_DELAY * (2 ** ((attempt - 1) % 4))  # 2, 4, 8, 16, 2, 4, 8, 16...
                        print(f"MPU6050: Повтор через {delay} сек...", flush=True)
                        time.sleep(delay)
                
                if not initialized:
                    print("MPU6050: Не удалось инициализировать после всех попыток", flush=True)
                    error_count += 1
                    time.sleep(5)
                    continue
                
                error_count = 0

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

            error_count = 0

            # Ожидание с проверкой shutdown_flag
            for _ in range(DELAY):
                if shutdown_flag:
                    break
                time.sleep(1)

        except (OSError, IOError) as e:
            print(f"Ошибка I2C: {e}. Переподключение...", flush=True)
            close_bus(bus)
            bus = None
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
            close_bus(bus)
            bus = None
            error_count = 0
            time.sleep(5)
    
    # Гарантированное закрытие SMBus
    close_bus(bus)
    print("[MPU6050] Завершён.", flush=True)
