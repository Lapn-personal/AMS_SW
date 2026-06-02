#!/usr/bin/env python3 -u
"""
wind_worker_ads1115.py — измерение скорости ветра через ADS1115.

Использует adafruit_ads1x15 + busio.I2C для работы с датчиком,
с предварительной диагностикой шины через i2c-tools.
"""

import time
import json
import sys
import signal
import paho.mqtt.client as mqtt
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn

# Импортируем I2C-хелперы для стабильности шины
from i2c_helpers import (
    create_i2c_bus,
    ensure_i2c_ready,
    i2c_bus_reset,
    i2c_recover,
)

# ========== НАСТРОЙКИ MQTT ==========
MQTT_BROKER = "127.0.0.1"
MQTT_PORT = 1883
MQTT_USER = "ams_iot"
MQTT_PASS = "ams_iot_pass"
MQTT_TOPIC_WIND = "sensors/wind"

# ========== АДРЕС ADS1115 ==========
ADS1115_ADDR = 0x48

# ========== КАЛИБРОВКА ДАТЧИКА ВЕТРА (подберите свои значения) ==========
VOLTAGE_AT_ZERO_WIND = 0.02    # Вольт при 0 м/с
VOLTAGE_AT_MAX_WIND = 0.8      # Вольт при максимальной скорости
MAX_WIND_SPEED = 30.0          # Максимальная скорость (м/с)

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


# ========== ПОДКЛЮЧЕНИЕ К MQTT С ПОВТОРАМИ ==========
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
            print(f"ADS1115: Ошибка подключения к MQTT: {e}, повтор через 5 сек...")
            time.sleep(5)
    return None


# ========== ИНИЦИАЛИЗАЦИЯ ДАТЧИКА ==========
def init_sensor():
    """
    Инициализирует ADS1115 через adafruit_ads1x15 с повторными попытками.
    Перед каждой попыткой делает "встряску" шины.
    """
    last_error = None
    for attempt in range(1, MAX_I2C_RETRIES + 1):
        try:
            # Встряска шины перед каждой попыткой
            i2c_bus_reset()

            # Проверяем наличие устройства через i2c-tools
            if not ensure_i2c_ready(addr=ADS1115_ADDR, max_retries=2, delay=0.5):
                raise IOError("ADS1115 не обнаружен на шине")

            # Создаём шину busio и инициализируем датчик
            i2c_bus = create_i2c_bus(max_retries=2, retry_delay=0.5)
            if i2c_bus is None:
                raise IOError("Не удалось создать I2C-шину")

            ads = ADS.ADS1115(i2c_bus, address=ADS1115_ADDR)
            # Настраиваем канал A0 в single-ended режиме (канал 0)
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

    print(f"ADS1115: Не удалось инициализировать после {MAX_I2C_RETRIES} попыток: {last_error}")
    return None, None


def read_voltage(channel):
    """
    Читает напряжение с ADS1115 через AnalogIn.
    Возвращает float (вольты) или None при ошибке.
    """
    try:
        return channel.voltage
    except Exception as e:
        print(f"ADS1115: Ошибка чтения напряжения: {e}")
        return None


def signal_handler(signum, frame):
    global shutdown_flag
    print(f"\n[ADS1115] Получен сигнал {signum}. Завершаем работу...")
    shutdown_flag = True


# ========== MAIN ==========
if __name__ == "__main__":
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    print("Wind worker (ADS1115) запущен")
    client = connect_mqtt()
    if client is None:
        sys.exit(1)
    
    ads = None
    channel = None
    error_count = 0

    while not shutdown_flag:
        try:
            # Проверка MQTT
            if not client.is_connected():
                print("ADS1115: MQTT разорван, переподключаемся...")
                client = connect_mqtt()
                if client is None:
                    break
                continue

            # Инициализация датчика, если ещё не
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
            print(f"[ВЕТЕР ADS1115] Напряжение: {voltage:.4f} В -> {wind_speed:.1f} м/с")
            
            error_count = 0  # сброс счётчика после успешного чтения
            
            # Ожидание с проверкой shutdown_flag
            for _ in range(3):
                if shutdown_flag:
                    break
                time.sleep(1)

        except Exception as e:
            print(f"ADS1115: Ошибка чтения/инициализации: {e}")
            ads = None
            channel = None
            error_count += 1
            time.sleep(3)
        
        # Если слишком много ошибок подряд — полный перезапуск I2C
        if error_count >= MAX_ERRORS_BEFORE_RESTART:
            print(f"ADS1115: {error_count} ошибок подряд. Полная переинициализация I2C...")
            i2c_recover()
            ads = None
            channel = None
            error_count = 0
            time.sleep(5)
    
    print("[ADS1115] Завершён.")