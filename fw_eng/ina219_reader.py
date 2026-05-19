#!/usr/bin/env python3 -u
import time
import json
import paho.mqtt.client as mqtt
from ina219 import INA219
from ina219 import DeviceRangeError

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

def mv_to_wind_speed(mv):
    if mv <= 0:
        return 0.0
    if mv >= VOLTAGE_MAX_MV:
        return WIND_MAX_MPS
    return (mv / VOLTAGE_MAX_MV) * WIND_MAX_MPS

# MQTT
client = mqtt.Client()
client.username_pw_set(MQTT_USER, MQTT_PASS)
client.connect(MQTT_BROKER, MQTT_PORT)
client.loop_start()

# INA219 – специальная настройка для малых напряжений
ina = INA219(SHUNT_OHMS, busnum=1)
# Установим большой диапазон напряжения (32V) и автоматическое усиление, чтобы измерить даже милливольты
ina.configure(voltage_range=ina.RANGE_32V, gain=ina.GAIN_AUTO, bus_adc=ina.ADC_12BIT, shunt_adc=ina.ADC_12BIT)

print("Wind worker: измеряем напряжение с аналогового датчика ветра (динамо)")

try:
    while True:
        # Читаем дифференциальное напряжение (Vin+ - Vin-)
        voltage_v = ina.voltage()
        voltage_mv = voltage_v * 1000

        if voltage_mv < 0:
            voltage_mv = 0

        wind_speed = mv_to_wind_speed(voltage_mv)

        payload = {"wind_speed_mps": round(wind_speed, 1)}
        client.publish(MQTT_TOPIC_WIND, json.dumps(payload), qos=0)

        print(f"[ВЕТЕР] Напряжение: {voltage_mv:.0f} мВ -> {wind_speed:.1f} м/с")
        time.sleep(1)

except DeviceRangeError as e:
    print(f"Ошибка INA219: {e}")
except KeyboardInterrupt:
    print("\nWind worker завершён.")