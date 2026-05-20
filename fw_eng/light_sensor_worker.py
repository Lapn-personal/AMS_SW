#!/usr/bin/env python3
import time
import json
import paho.mqtt.client as mqtt
import busio
import adafruit_tsl2561

MQTT_HOST = "127.0.0.1"
MQTT_PORT = 1883
MQTT_USER = "ams_iot"
MQTT_PASS = "ams_iot_pass"

TOPIC = "sensors/illuminance"
INTERVAL = 3  # секунд

def main():
    # Инициализация I2C и датчика
    i2c = busio.I2C(3, 2)  # SCL=3, SDA=2
    sensor = adafruit_tsl2561.TSL2561(i2c, address=0x29)

    client = mqtt.Client()
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.connect(MQTT_HOST, MQTT_PORT)
    client.loop_start()

    print("Скрипт освещённости TSL2561 запущен")
    while True:
        lux = sensor.lux
        if lux is not None:
            payload = json.dumps({
                "lux": round(lux, 2)
            })
            client.publish(TOPIC, payload, qos=0)
            print(f"Published {TOPIC}: {lux:.2f} lx")
        else:
            payload = json.dumps({
                "lux": ("None")
            })
            client.publish(TOPIC, payload, qos=0)
            print("Ошибка чтения освещённости")
        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()