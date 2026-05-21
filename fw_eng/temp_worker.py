#!/usr/bin/env python3
import os
import time
import json
import sys
import signal
import paho.mqtt.client as mqtt

# Конфигурация локального MQTT
MQTT_HOST = "127.0.0.1"
MQTT_PORT = 1883
MQTT_USER = "ams_iot"
MQTT_PASS = "ams_iot_pass"

TOPIC_TEMPLATE = "sensors/temperature/{}"
INTERVAL = 3  # секунд

shutdown_flag = False

def find_soc_temp_file():
    """Ищет файл температуры процессора."""
    candidates = [
        '/sys/class/thermal/thermal_zone0/temp',
        '/sys/class/thermal/thermal_zone1/temp',
        '/sys/class/hwmon/hwmon0/temp1_input',  # альтернатива
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None

def read_soc_temp():
    """Читает температуру бортового датчика SoC."""
    SOC_TEMP_FILE = find_soc_temp_file()
    if SOC_TEMP_FILE is None:
        return None
    try:
        with open(SOC_TEMP_FILE, 'r') as f:
            temp_millideg = int(f.read().strip())
            return round(temp_millideg / 1000.0, 2)
    except Exception:
        return None
    
def find_all_1wire_sensors():
    base_dir = '/sys/bus/w1/devices/'
    sensors = []
    try:
        for entry in os.listdir(base_dir):
            if entry == 'w1_bus_master1':
                continue
            path = os.path.join(base_dir, entry)
            if os.path.isdir(path):
                if os.path.exists(os.path.join(path, 'temperature')) or \
                   os.path.exists(os.path.join(path, 'w1_slave')):
                    sensors.append(entry)
    except Exception as e:
        print(f"Ошибка сканирования 1-Wire: {e}")
    return sensors

def read_1wire_temperature(sensor_id):
    base_path = f'/sys/bus/w1/devices/{sensor_id}'
    temp_file = os.path.join(base_path, 'temperature')
    if os.path.exists(temp_file):
        try:
            with open(temp_file, 'r') as f:
                raw = f.read().strip()
                return round(float(raw) / 1000.0, 2)
        except Exception:
            pass
    slave_file = os.path.join(base_path, 'w1_slave')
    if os.path.exists(slave_file):
        try:
            with open(slave_file, 'r') as f:
                lines = f.readlines()
            if 'YES' not in lines[0]:
                return None
            for line in lines:
                if 't=' in line:
                    temp_str = line.split('t=')[1]
                    temp_c = int(temp_str) / 1000.0
                    return round(temp_c, 2)
        except Exception:
            pass
    return None

def connect_mqtt():
    """Подключается к MQTT с бесконечными повторами."""
    client = mqtt.Client()
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    while not shutdown_flag:
        try:
            client.connect(MQTT_HOST, MQTT_PORT)
            client.loop_start()
            print("Temp worker: MQTT подключён")
            return client
        except Exception as e:
            print(f"Temp worker: Ошибка подключения к MQTT: {e}, повтор через 5 сек...")
            time.sleep(5)
    return None

def signal_handler(signum, frame):
    global shutdown_flag
    print(f"\n[TEMP] Получен сигнал {signum}. Завершаем работу...")
    shutdown_flag = True

def main():
    global shutdown_flag
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    client = connect_mqtt()
    if client is None:
        return

    print("Скрипт 1-Wire температуры запущен")
    while not shutdown_flag:
        # Проверка соединения с MQTT
        if not client.is_connected():
            print("Temp worker: MQTT разорван, переподключаемся...")
            client = connect_mqtt()
            if client is None:
                break
            continue

        sensors = find_all_1wire_sensors()
        for sid in sensors:
            if shutdown_flag:
                break
            hwd_temp = read_soc_temp()
            out_temp = read_1wire_temperature(sid)
            if out_temp is not None:
                payload = json.dumps({
                    "hdw_temperature": hwd_temp,
                    "out_temperature": out_temp
                })
                topic = TOPIC_TEMPLATE.format(sid)
                try:
                    client.publish(topic, payload, qos=0)
                    print(f"Published {topic}: HDW-TEMP:{hwd_temp}°C, OUT-TEMP:{out_temp}°C")
                except Exception as e:
                    print(f"Ошибка публикации {topic}: {e}")
            else:
                print(f"Ошибка чтения {sid}")
        
        # Ожидание с проверкой shutdown_flag
        for _ in range(INTERVAL):
            if shutdown_flag:
                break
            time.sleep(1)
    
    print("[TEMP] Завершён.")

if __name__ == "__main__":
    main()
