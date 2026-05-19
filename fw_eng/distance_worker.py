#!/usr/bin/env python3 -u
import time
import json
import board
import busio
import adafruit_vl53l0x
import paho.mqtt.client as mqtt

# ========== НАСТРОЙКИ MQTT ==========
MQTT_BROKER = "127.0.0.1"
MQTT_PORT = 1883
MQTT_USER = "ams_iot"
MQTT_PASS = "ams_iot_pass"
MQTT_TOPIC = "sensors/distance"

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
    sensor = adafruit_vl53l0x.VL53L0X(i2c)
    # Настройка бюджета времени (опционально)
    # sensor.measurement_timing_budget = 50000  # 50 мс
    print("Датчик VL53L0X инициализирован")
    return sensor

print("Distance worker (VL53L0X) запущен")
client = connect_mqtt()
sensor = None

while True:
    try:
        if sensor is None:
            sensor = init_sensor()
        range_mm = sensor.range
        if range_mm is not None:
            payload = {"distance_mm": range_mm}
            client.publish(MQTT_TOPIC, json.dumps(payload), qos=0)
            print(f"[РАССТОЯНИЕ] {range_mm} мм")
        else:
            print("[РАССТОЯНИЕ] Ошибка измерения")
        time.sleep(2)
    except Exception as e:
        print(f"Ошибка чтения/датчика: {e}")
        sensor = None  # сбросим, чтобы при следующей итерации переинициализировать
        time.sleep(3)
    # Проверка соединения с MQTT (если отвалился, переподключимся)
    if not client.is_connected():
        print("MQTT разорван, переподключаемся...")
        client = connect_mqtt()