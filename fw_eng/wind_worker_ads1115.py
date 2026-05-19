#!/usr/bin/env python3 -u
import time
import json
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
VOLTAGE_AT_ZERO_WIND = 0.00    # Вольт при 0 м/с
VOLTAGE_AT_MAX_WIND = 0.8      # Вольт при максимальной скорости
MAX_WIND_SPEED = 30.0          # Максимальная скорость (м/с)

def voltage_to_wind_speed(voltage):
    if voltage <= VOLTAGE_AT_ZERO_WIND:
        return 0.0
    if voltage >= VOLTAGE_AT_MAX_WIND:
        return MAX_WIND_SPEED
    return (voltage - VOLTAGE_AT_ZERO_WIND) * (MAX_WIND_SPEED / (VOLTAGE_AT_MAX_WIND - VOLTAGE_AT_ZERO_WIND))

# ========== ПОДКЛЮЧЕНИЕ К MQTT С ПОВТОРАМИ ==========
def connect_mqtt():
    client = mqtt.Client()
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    while True:
        try:
            client.connect(MQTT_BROKER, MQTT_PORT)
            client.loop_start()
            print("MQTT подключён")
            return client
        except Exception as e:
            print(f"Ошибка подключения к MQTT: {e}, повтор через 5 сек...")
            time.sleep(5)

# ========== ИНИЦИАЛИЗАЦИЯ ДАТЧИКА ==========
def init_sensor():
    i2c = busio.I2C(board.SCL, board.SDA)
    ads = ADS1115(i2c)
    ads.gain = 1   # диапазон ±4.096 В
    # Дифференциальный канал между A0 и A1
    chan = AnalogIn(ads, ads1x15.Pin.A0, ads1x15.Pin.A1)
    print("ADS1115 инициализирован")
    return ads, chan

print("Wind worker (ADS1115) запущен")
client = connect_mqtt()
ads, chan = None, None

while True:
    try:
        if ads is None:
            ads, chan = init_sensor()
        voltage = chan.voltage
        wind_speed = voltage_to_wind_speed(voltage)
        payload = {"wind_speed_mps": round(wind_speed, 1)}
        client.publish(MQTT_TOPIC_WIND, json.dumps(payload), qos=0)
        print(f"[ВЕТЕР] Напряжение: {voltage:.2f} В -> {wind_speed:.1f} м/с")
        time.sleep(2)
    except Exception as e:
        print(f"Ошибка чтения/инициализации ADS1115: {e}")
        ads = None  # сбросим, чтобы пересоздать при следующем цикле
        time.sleep(3)
    if not client.is_connected():
        print("MQTT разорван, переподключаемся...")
        client = connect_mqtt()