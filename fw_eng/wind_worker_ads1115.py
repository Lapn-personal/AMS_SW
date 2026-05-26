#!/usr/bin/env python3 -u
"""
wind_worker_ads1115.py — измерение скорости ветра через ADS1115.

Использует i2cget/i2cset (i2c-tools) для максимальной стабильности,
вместо adafruit_ads1x15 + busio.I2C, которые нестабильны на Debian 13.
"""

import time
import json
import sys
import signal
import paho.mqtt.client as mqtt

# Импортируем I2C-хелперы
from i2c_helpers import (
    ensure_i2c_ready,
    i2c_read_word,
    i2c_write_block,
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
REG_CONFIG = 0x01
REG_CONVERSION = 0x00

# ========== КАЛИБРОВКА ДАТЧИКА ВЕТРА (подберите свои значения) ==========
VOLTAGE_AT_ZERO_WIND = 0.02    # Вольт при 0 м/с
VOLTAGE_AT_MAX_WIND = 0.8      # Вольт при максимальной скорости
MAX_WIND_SPEED = 30.0          # Максимальная скорость (м/с)

# ========== ЗАЩИТА ОТ ЗАВИСАНИЙ ==========
MAX_I2C_RETRIES = 5
I2C_RETRY_DELAY = 3
MAX_ERRORS_BEFORE_RESTART = 10

# Конфигурация ADS1115:
# - Continuous conversion (бит 15 = 0)
# - A0-A1 differential (MUX = 000)
# - ±4.096V (PGA = 001, gain=1)
# - 128 SPS (DR = 100)
# Байты для i2cset block: [0x83, 0x06] (старший, младший)
ADS1115_CONFIG_BYTES = [0x83, 0x06]

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
    Инициализирует ADS1115 через i2cset/i2cget с повторными попытками.
    Перед каждой попыткой делает "встряску" шины.
    """
    last_error = None
    for attempt in range(1, MAX_I2C_RETRIES + 1):
        try:
            # Встряска шины перед каждой попыткой
            i2c_bus_reset()
            
            # Проверяем, что устройство отвечает
            config_val = i2c_read_word(ADS1115_ADDR, REG_CONFIG, max_retries=2, delay=0.3)
            if config_val is None:
                raise IOError("ADS1115 не отвечает на шине")
            
            # Настраиваем конфиг: continuous mode, A0-A1, ±4.096V, 128 SPS
            if not i2c_write_block(ADS1115_ADDR, REG_CONFIG, ADS1115_CONFIG_BYTES, max_retries=2, delay=0.3):
                raise IOError("Не удалось записать конфиг ADS1115")
            
            # Проверяем, что конфиг применился
            time.sleep(0.1)
            config_val = i2c_read_word(ADS1115_ADDR, REG_CONFIG, max_retries=2, delay=0.3)
            if config_val is None:
                raise IOError("ADS1115 перестал отвечать после записи конфига")
            
            print(f"ADS1115: инициализирован (попытка {attempt}), config=0x{config_val:04X}")
            return True
            
        except Exception as e:
            last_error = e
            print(f"ADS1115: Ошибка инициализации (попытка {attempt}/{MAX_I2C_RETRIES}): {e}")
            if attempt < MAX_I2C_RETRIES:
                delay = I2C_RETRY_DELAY * (2 ** (attempt - 1))
                print(f"ADS1115: Повтор через {delay} сек...")
                time.sleep(delay)
    
    print(f"ADS1115: Не удалось инициализировать после {MAX_I2C_RETRIES} попыток: {last_error}")
    return False


def read_voltage():
    """
    Читает напряжение с ADS1115.
    Возвращает float (вольты) или None при ошибке.
    """
    raw = i2c_read_word(ADS1115_ADDR, REG_CONVERSION, max_retries=3, delay=0.3)
    if raw is None:
        return None
    # ±4.096V, 16-bit: 1 LSB = 4.096 / 32768 = 0.000125V
    voltage = raw * 4.096 / 32768.0
    return voltage


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
    
    initialized = False
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
            if not initialized:
                initialized = init_sensor()
                if not initialized:
                    print("ADS1115: Датчик недоступен, повтор через 10 сек...")
                    time.sleep(10)
                    continue
                error_count = 0

            voltage = read_voltage()
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
            initialized = False  # сбросим, чтобы пересоздать при следующем цикле
            error_count += 1
            time.sleep(3)
        
        # Если слишком много ошибок подряд — полный перезапуск I2C
        if error_count >= MAX_ERRORS_BEFORE_RESTART:
            print(f"ADS1115: {error_count} ошибок подряд. Полная переинициализация I2C...")
            i2c_recover()
            initialized = False
            error_count = 0
            time.sleep(5)
    
    print("[ADS1115] Завершён.")
