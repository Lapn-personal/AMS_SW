#!/usr/bin/env python3
import time
import json
import threading
import paho.mqtt.client as mqtt

# Локальный брокер
LOCAL_BROKER = "127.0.0.1"
LOCAL_PORT = 1883
LOCAL_USER = "ams_iot"
LOCAL_PASS = "ams_iot_pass"

# Удалённый брокер (замените на свои данные)
REMOTE_BROKER = "158.160.208.22"
REMOTE_PORT = 1883
REMOTE_USER = "ams"
REMOTE_PASS = "ams_32Kek"
DEVICE_ID = "AMS-000001"
TOPIC_REMOTE = f"ams/{DEVICE_ID}/telemetry"

INTERVAL = 10  # секунд – отправка раз в 3 минуты

# Хранилище последних показаний датчиков
latest_data = {
    "temperatures": {},  # {sensor_id: temp}
    "illuminance": None,
    # сюда добавим другие датчики
}
last_update = {}

def on_local_message(client, userdata, msg):
    topic = msg.topic
    try:
        payload = json.loads(msg.payload.decode())
        if topic.startswith("sensors/temperature/"):
            sensor_id = topic.split("/")[-1]
            hdw_temp = payload.get("hdw_temperature")
            out_temp = payload.get("out_temperature")
            if hdw_temp is not None:
                latest_data["temperatures"][sensor_id] = f"HDW:{hdw_temp}, OUT:{out_temp}"
                last_update[f"temp_{sensor_id}"] = time.time()
        elif topic == "sensors/illuminance":
            lux = payload.get("lux")
            if lux is not None:
                latest_data["illuminance"] = lux
                last_update["illuminance"] = time.time()
        # Добавить другие топики по мере появления
    except Exception as e:
        print(f"Ошибка обработки сообщения {topic}: {e}")

def send_aggregated():
    """Формирует JSON и отправляет на удалённый брокер"""
    payload = {
        "device_id": DEVICE_ID,
        "temperatures": latest_data["temperatures"].copy(),
        "illuminance_lux": latest_data["illuminance"],
        # можно добавить uptime и другие метрики
    }
    # Удаляем None значения (если датчик не опрошен)
    # if payload["illuminance_lux"] is None:
        # del payload["illuminance_lux"]
    # if not payload["temperatures"]:
        # del payload["temperatures"]

    try:
        remote_client.publish(TOPIC_REMOTE, json.dumps(payload), qos=1)
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Отправлено: {len(payload.get('temperatures', {}))} температур, lux={payload.get('illuminance_lux')}")
    except Exception as e:
        print(f"Ошибка отправки на удалённый брокер: {e}")

def remote_loop():
    """Цикл отправки агрегированных данных"""
    while True:
        send_aggregated()
        time.sleep(INTERVAL)

if __name__ == "__main__":
    # Подключаемся к локальному брокеру
    local_client = mqtt.Client()
    local_client.username_pw_set(LOCAL_USER, LOCAL_PASS)
    local_client.on_message = on_local_message
    local_client.connect(LOCAL_BROKER, LOCAL_PORT)
    local_client.subscribe("sensors/#")
    local_client.loop_start()

    # Подключаемся к удалённому брокеру
    remote_client = mqtt.Client()
    remote_client.username_pw_set(REMOTE_USER, REMOTE_PASS)
    remote_client.connect(REMOTE_BROKER, REMOTE_PORT)
    remote_client.loop_start()

    print("Агрегатор запущен. Ожидание данных от датчиков...")
    remote_loop()