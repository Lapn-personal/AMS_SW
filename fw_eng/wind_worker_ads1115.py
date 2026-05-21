#!/usr/bin/env python3 -u
import time
import json
import sys
import signal
import board
import busio
from adafruit_ads1x15.ads1115 import ADS1115
from adafruit_ads1x15.analog_in import AnalogIn
from adafruit_ads1x15 import ads1x15  # для констант Pin.A0, Pin.A1
import paho.mqtt.client as mqtt

# ========== НАСТРОЙКИ MQTT ==========
MQTT_BROKER = "127.0.0.1"
MQTT_PORT = 1883
MQTT_USER = "ams_iot"
MQTT_PASS = "ams_iot_pass"
MQTT_TOPIC_WIND = "sensors/wind"

# ========== КАЛИБРОВКА ДАТЧИКА ВЕТРА (подберите свои значения) ==========
VOLTAGE_AT_ZERO_WIND = 0.02    # Вольт при 0 м/с
VOLTAGE_AT_MAX_WIND = 0.8      # Вольт при максимальной скорости
MAX_WIND_SPEED = 30.0          # Максимальная скорость (м/с)

# ========== ЗАЩИТА ОТ ЗАВИСАНИЙ ==========
MAX_I2C_RETRIES = 5            # макс. попыток инициализации I2C
I2C_RETRY_DELAY = 3            # задержка между попытками (сек)
MAX_ERRORS_BEFORE_RESTART = 10 # если N ошибок подряд — полный перезапуск I2C

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
    """Инициализирует ADS1115 с повторными попытками при ошибках I2C."""
    last_error = None
    for attempt in range(1, MAX_I2C_RETRIES + 1):
        try:
            i2c = busio.I2C(board.SCL, board.SDA)
            ads = ADS1115(i2c)
            ads.gain = 1   # диапазон ±4.096 В
            # Дифференциальный канал между A0 и A1
            chan = AnalogIn(ads, ads1x15.Pin.A0, ads1x15.Pin.A1)
            print(f"ADS1115: инициализирован (попытка {attempt})")
            return ads, chan
        except Exception as e:
            last_error = e
            print(f"ADS1115: Ошибка инициализации (попытка {attempt}/{MAX_I2C_RETRIES}): {e}")
            if attempt < MAX_I2C_RETRIES:
                # Экспоненциальная задержка: 3, 6, 12, 24 сек
                delay = I2C_RETRY_DELAY * (2 ** (attempt - 1))
                print(f"ADS1115: Повтор через {delay} сек...")
                time.sleep(delay)
    print(f"ADS1115: Не удалось инициализировать после {MAX_I2C_RETRIES} попыток: {last_error}")
    return None, None

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
    
    ads, chan = None, None
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
            if ads is None:
                ads, chan = init_sensor()
                if ads is None:
                    # Если не удалось инициализировать — ждём и пробуем снова
                    print("ADS1115: Датчик недоступен, повтор через 10 сек...")
                    time.sleep(10)
                    continue
                error_count = 0

            voltage = chan.voltage
            wind_speed = voltage_to_wind_speed(voltage)
            payload = {"wind_speed_mps": round(wind_speed, 1)}
            client.publish(MQTT_TOPIC_WIND, json.dumps(payload), qos=0)
            print(f"[ВЕТЕР ADS1115] Напряжение: {voltage:.2f} В -> {wind_speed:.1f} м/с")
            
            error_count = 0  # сброс счётчика после успешного чтения
            
            # Ожидание с проверкой shutdown_flag
            for _ in range(3):
                if shutdown_flag:
                    break
                time.sleep(1)

        except Exception as e:
            print(f"ADS1115: Ошибка чтения/инициализации: {e}")
            ads = None  # сбросим, чтобы пересоздать при следующем цикле
            error_count += 1
            time.sleep(3)
        
        # Если слишком много ошибок подряд — полный перезапуск I2C
        if error_count >= MAX_ERRORS_BEFORE_RESTART:
            print(f"ADS1115: {error_count} ошибок подряд. Полная переинициализация I2C...")
            ads = None
            chan = None
            error_count = 0
            time.sleep(5)
    
    print("[ADS1115] Завершён.")
