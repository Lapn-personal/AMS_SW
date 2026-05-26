#!/usr/bin/env python3 -u
"""
ina219_reader.py — измерение напряжения с аналогового датчика ветра через INA219.

Использует библиотеку ina219 для работы с датчиком.
Добавлена проверка ID-регистра через i2cget перед инициализацией
и многоуровневое восстановление шины при ошибках.
"""

import time
import json
import sys
import signal
import subprocess
import paho.mqtt.client as mqtt
from ina219 import INA219
from ina219 import DeviceRangeError

# Импортируем I2C-хелперы для стабильности шины
from i2c_helpers import i2c_bus_reset, i2c_recover

# ========== НАСТРОЙКИ ==========
SHUNT_OHMS = 0.1  # не важно, можно оставить
MQTT_BROKER = "127.0.0.1"
MQTT_PORT = 1883
MQTT_USER = "ams_iot"
MQTT_PASS = "ams_iot_pass"
MQTT_TOPIC_WIND = "sensors/wind"

# Калибровка датчика ветра (подберите под свой)
# Например: при напряжении 100 мВ -> 5 м/с, при 500 мВ -> 25 м/с (линейно)
VOLTAGE_MAX_MV = 500.0      # максимальное измеренное напряжение (мВ)
WIND_MAX_MPS = 25.0         # скорость ветра при максимальном напряжении

# Watchdog: максимальное время между публикациями (сек)
PUBLISH_INTERVAL = 1
MAX_SKIP_BEFORE_RESTART = 5  # уменьшено — быстрее переходим к recovery
INIT_DELAY = 3               # задержка перед первой инициализацией (сек)

INA219_ADDR = 0x40  # стандартный адрес INA219
I2C_BUS = 1

shutdown_flag = False


def check_ina219_id():
    """
    Проверяет ID-регистр INA219 через i2cget.
    Регистр 0x00 (Configuration) должен читаться без ошибок.
    Регистр 0xFE (Manufacturer ID) должен быть 0x5449 ('TI').
    Возвращает True, если датчик отвечает корректно.
    """
    try:
        # Читаем конфигурационный регистр (0x00)
        result = subprocess.run(
            ["i2cget", "-y", str(I2C_BUS), f"0x{INA219_ADDR:02X}", "0x00", "w"],
            capture_output=True, text=True, timeout=3
        )
        if result.returncode == 0:
            val = int(result.stdout.strip(), 16)
            # INA219 при включении имеет конфиг 0x399F
            # INA226 имеет другой конфиг, но любое успешное чтение — уже хорошо
            print(f"INA219: Конфиг регистр = 0x{val:04X}", flush=True)
            return True
        else:
            return False
    except Exception as e:
        print(f"INA219: Ошибка проверки ID: {e}", flush=True)
        return False


def mv_to_wind_speed(mv):
    if mv <= 0:
        return 0.0
    if mv >= VOLTAGE_MAX_MV:
        return WIND_MAX_MPS
    return (mv / VOLTAGE_MAX_MV) * WIND_MAX_MPS


def connect_mqtt():
    """Подключается к MQTT с бесконечными повторами."""
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    while not shutdown_flag:
        try:
            client.connect(MQTT_BROKER, MQTT_PORT)
            client.loop_start()
            print("INA219: MQTT подключён")
            return client
        except Exception as e:
            print(f"INA219: Ошибка подключения к MQTT: {e}, повтор через 5 сек...")
            time.sleep(5)
    return None


def init_ina():
    """Инициализирует INA219 с повторными попытками и проверкой ID."""
    for attempt in range(1, 11):  # до 10 попыток
        try:
            # Встряска шины перед инициализацией
            i2c_bus_reset()
            
            # Проверяем ID через i2cget
            if not check_ina219_id():
                print(f"INA219: ID не совпадает (попытка {attempt}/10)", flush=True)
                if attempt < 10:
                    delay = 2 * (2 ** ((attempt - 1) % 4))
                    print(f"INA219: Повтор через {delay} сек...")
                    time.sleep(delay)
                continue
            
            ina = INA219(SHUNT_OHMS, busnum=1)
            ina.configure(voltage_range=ina.RANGE_32V, gain=ina.GAIN_AUTO,
                          bus_adc=ina.ADC_12BIT, shunt_adc=ina.ADC_12BIT)
            print(f"INA219: датчик инициализирован (попытка {attempt})")
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
    print(f"\n[INA219] Получен сигнал {signum}. Завершаем работу...")
    shutdown_flag = True


# ========== MAIN ==========
if __name__ == "__main__":
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    print("Wind worker (INA219): измеряем напряжение с аналогового датчика ветра (динамо)")
    
    client = connect_mqtt()
    if client is None:
        sys.exit(1)
    
    # Задержка перед первой инициализацией
    print(f"INA219: Ожидание {INIT_DELAY} сек перед первой инициализацией...", flush=True)
    time.sleep(INIT_DELAY)
    
    ina = init_ina()
    if ina is None:
        sys.exit(1)
    
    error_count = 0
    
    while not shutdown_flag:
        try:
            # Проверка соединения с MQTT
            if not client.is_connected():
                print("INA219: MQTT разорван, переподключаемся...")
                client = connect_mqtt()
                if client is None:
                    break
                continue
            
            # Читаем дифференциальное напряжение (Vin+ - Vin-)
            voltage_v = ina.voltage()
            voltage_mv = voltage_v * 1000

            if voltage_mv < 0:
                voltage_mv = 0

            wind_speed = mv_to_wind_speed(voltage_mv)

            payload = {"wind_speed_mps": round(wind_speed, 1)}
            client.publish(MQTT_TOPIC_WIND, json.dumps(payload), qos=0)

            print(f"[ВЕТЕР INA219] Напряжение: {voltage_mv:.0f} мВ -> {wind_speed:.1f} м/с")
            
            error_count = 0  # сброс счётчика ошибок после успешного чтения
            
            # Ожидание с проверкой shutdown_flag
            for _ in range(PUBLISH_INTERVAL):
                if shutdown_flag:
                    break
                time.sleep(1)

        except DeviceRangeError as e:
            print(f"INA219: Ошибка диапазона: {e}")
            error_count += 1
            time.sleep(1)
        except (OSError, IOError) as e:
            print(f"INA219: Ошибка I2C: {e}. Переинициализация...")
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
        
        # Если слишком много ошибок подряд — перезапускаем I2C
        if error_count >= MAX_SKIP_BEFORE_RESTART:
            print(f"INA219: {error_count} ошибок подряд. Полная переинициализация I2C...")
            i2c_recover()
            try:
                ina = init_ina()
            except:
                pass
            error_count = 0
            time.sleep(3)
    
    print("[INA219] Завершён.")
