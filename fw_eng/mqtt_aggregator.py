#!/usr/bin/env python3
import time
import json
import threading
import random
import sys
import os
import signal
from datetime import datetime, timezone
import paho.mqtt.client as mqtt

# ========== ВЕРСИЯ ПРОЕКТА ==========
try:
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fw_settings", "VERSION")) as f:
        PROJECT_VERSION = f.read().strip()
except:
    PROJECT_VERSION = "unknown"

# ========== НАСТРОЙКИ ==========
LOCAL_BROKER = "127.0.0.1"
LOCAL_PORT = 1883
LOCAL_USER = "ams_iot"
LOCAL_PASS = "ams_iot_pass"

REMOTE_BROKER = "81.26.179.30"
REMOTE_PORT = 1883
REMOTE_USER = "ams"
REMOTE_PASS = "ams_32Kek"

# Файл для хранения постоянного UID
UID_FILE = os.path.expanduser("~/fw_settings/uid.json")
SETTINGS_DIR = os.path.expanduser("~/fw_settings")

INTERVAL = 60
EMULATION_ENABLED = True

THRESHOLD_OVERHEAT = 85.0
WIND_ALERT_THRESHOLD = 10.0
THRESHOLD_TILT = 5.0
COOLDOWN_SECONDS = 120

# Watchdog: максимальное время без данных от каждого воркера (сек)
WATCHDOG_TIMEOUT = {
    "temperature": 120,   # ожидаем раз в 3 сек * 40 = 120 сек запаса
    "distance": 30,       # раз в 3 сек
    "wind": 30,           # раз в 3 сек
    "tilt": 30,           # раз в 3 сек
    "power": 120,         # раз в 30 сек (эмуляция)
    "battery": 120,       # раз в 30 сек (эмуляция)
}
WATCHDOG_CHECK_INTERVAL = 60  # проверка раз в минуту

# Таймаут регистрации (сек) — если за это время не получен UID, перезапускаемся
REGISTRATION_TIMEOUT = 300  # 5 минут

# Глобальные переменные для регистрации
DEVICE_ID = None
TOPIC_REMOTE_TELEMETRY = None
TOPIC_REMOTE_ALERT = None
TOPIC_COMMAND = None
current_temp_uid = None          # временный UID в процессе регистрации

# Хранилище данных
latest_data = {
    "temperatures": {},
    "distance": None,
    "power_phases": {"L1": None, "L2": None, "L3": None},
    "battery": {"battery_state": None},
    "wind": {"wind_speed_mps": None},
    "tilt": {"tilt_degrees": None}
}

# Watchdog: время последнего обновления каждого типа данных
last_data_time = {
    "temperature": 0,
    "distance": 0,
    "wind": 0,
    "tilt": 0,
    "power": 0,
    "battery": 0,
}

prev_power_state = {
    "phases": {"L1": None, "L2": None, "L3": None},
    "battery": None
}
last_alert_time = {
    "overheat": 0,
    "high_wind": 0,
    "excessive_tilt": 0,
    "power_state_change": 0,
    "watchdog": 0
}

# Флаг для graceful shutdown
shutdown_flag = False

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def get_iso_timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def get_mac_address():
    for iface in ['eth0', 'wlan0']:
        try:
            mac = open(f'/sys/class/net/{iface}/address').read().strip()
            if mac and mac != '00:00:00:00:00:00':
                return mac
        except:
            pass
    return "00:00:00:00:00:00"

def generate_temp_uid():
    mac = get_mac_address().replace(':', '')[-6:]
    rand_part = str(random.randint(1000, 9999))
    return f"TEMP-{mac}-{rand_part}"

def load_or_create_uid():
    if not os.path.exists(UID_FILE):
        return None
    try:
        with open(UID_FILE, 'r') as f:
            data = json.load(f)
            return data.get("uid")
    except:
        return None

def save_uid(uid):
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    with open(UID_FILE, 'w') as f:
        json.dump({"uid": uid}, f, indent=2)
    print(f"[РЕГИСТРАЦИЯ] Постоянный UID сохранён: {uid}")

def set_device_id(uid):
    global DEVICE_ID, TOPIC_REMOTE_TELEMETRY, TOPIC_REMOTE_ALERT, TOPIC_COMMAND
    DEVICE_ID = uid
    TOPIC_REMOTE_TELEMETRY = f"ams/{DEVICE_ID}/telemetry"
    TOPIC_REMOTE_ALERT = f"ams/{DEVICE_ID}/alert"
    TOPIC_COMMAND = f"ams/{DEVICE_ID}/command"
    print(f"[РЕГИСТРАЦИЯ] Устройство работает как {DEVICE_ID}")

def safe_remote_client():
    """Возвращает remote_client или None, если он ещё не инициализирован."""
    try:
        return remote_client
    except NameError:
        return None

# ========== WATCHDOG ==========
def check_watchdog():
    """Проверяет, не зависли ли воркеры. Если данные не обновлялись дольше таймаута — шлёт алерт."""
    now = time.time()
    remote = safe_remote_client()
    for data_type, timeout in WATCHDOG_TIMEOUT.items():
        last_time = last_data_time.get(data_type, 0)
        if last_time == 0:
            continue  # данные ещё ни разу не приходили
        elapsed = now - last_time
        if elapsed > timeout:
            # Шлём watchdog-алерт (не чаще раза в COOLDOWN_SECONDS)
            alert_key = f"watchdog_{data_type}"
            if now - last_alert_time.get(alert_key, 0) > COOLDOWN_SECONDS:
                last_alert_time[alert_key] = now
                msg = f"Watchdog: {data_type} не обновлялся {elapsed:.0f} сек (таймаут {timeout} сек)"
                print(f"[WATCHDOG] {msg}")
                if remote is not None and DEVICE_ID is not None:
                    try:
                        payload = {
                            "device_id": DEVICE_ID,
                            "alert_type": "watchdog",
                            "severity": "warning",
                            "value": round(elapsed, 0),
                            "threshold": timeout,
                            "message": msg,
                            "timestamp": get_iso_timestamp()
                        }
                        remote.publish(TOPIC_REMOTE_ALERT, json.dumps(payload), qos=1)
                    except Exception as e:
                        print(f"Ошибка отправки watchdog-алерта: {e}")

def watchdog_loop():
    """Фоновый поток проверки watchdog."""
    while not shutdown_flag:
        time.sleep(WATCHDOG_CHECK_INTERVAL)
        check_watchdog()

# ========== АЛЕРТЫ ==========
def send_alert(remote_client, alert_type, severity, value, threshold, message):
    now = time.time()
    if now - last_alert_time.get(alert_type, 0) < COOLDOWN_SECONDS:
        return
    last_alert_time[alert_type] = now
    payload = {
        "device_id": DEVICE_ID,
        "alert_type": alert_type,
        "severity": severity,
        "value": value,
        "threshold": threshold,
        "message": message,
        "timestamp": get_iso_timestamp()
    }
    try:
        remote_client.publish(TOPIC_REMOTE_ALERT, json.dumps(payload), qos=1)
        print(f"[АЛЕРТ] {alert_type}: {message}")
    except Exception as e:
        print(f"Ошибка отправки алерта: {e}")

def check_alerts(remote_client):
    if remote_client is None:
        return
    max_hdw = None
    for _, temp_str in latest_data["temperatures"].items():
        try:
            hdw = float(temp_str.split(',')[0].split(':')[1])
            if max_hdw is None or hdw > max_hdw:
                max_hdw = hdw
        except:
            pass
    if max_hdw is not None and max_hdw > THRESHOLD_OVERHEAT:
        send_alert(remote_client, "overheat", "critical", round(max_hdw, 1), THRESHOLD_OVERHEAT,
                   f"Перегрев оборудования: {max_hdw:.1f}°C")
    wind_speed = latest_data["wind"].get("wind_speed_mps")
    if wind_speed is not None and wind_speed > WIND_ALERT_THRESHOLD:
        send_alert(remote_client, "high_wind", "warning", round(wind_speed, 1), WIND_ALERT_THRESHOLD,
                   f"Сильный ветер: {wind_speed:.1f} м/с")
    tilt = latest_data["tilt"].get("tilt_degrees")
    if tilt is not None and tilt > THRESHOLD_TILT:
        send_alert(remote_client, "excessive_tilt", "critical", round(tilt, 1), THRESHOLD_TILT,
                   f"Критический наклон вышки: {tilt:.1f}°")
    current_phases = latest_data["power_phases"]
    current_battery = latest_data["battery"]["battery_state"]
    changed = False
    changes = []
    for phase in ["L1", "L2", "L3"]:
        old = prev_power_state["phases"].get(phase)
        new = current_phases.get(phase)
        if old is not None and new is not None and old != new:
            changed = True
            changes.append(f"Фаза {phase}: {old}→{new}")
    old_bat = prev_power_state["battery"]
    if old_bat is not None and current_battery is not None and old_bat != current_battery:
        changed = True
        changes.append(f"Батарея: {old_bat}→{current_battery}")
    prev_power_state["phases"] = current_phases.copy()
    prev_power_state["battery"] = current_battery
    if changed:
        send_alert(remote_client, "power_state_change", "warning", 0, 0,
                   "Изменение состояния питания: " + "; ".join(changes))

# ========== ЭМУЛЯЦИЯ ==========
def emulate_power_phases():
    return {f"L{i}": random.choice([True, True, True, False]) for i in [1,2,3]}

def emulate_battery():
    power = emulate_power_phases()
    if not any(power.values()):
        state = "discharging"
    elif any(power.values()):
        state = random.choice(["charging", "idle"])
    else:
        state = "idle"
    return {"battery_state": state}

def publish_emulated_sensors(local_client):
    power = emulate_power_phases()
    local_client.publish("sensors/power", json.dumps({"phases": power}), qos=0)
    battery = emulate_battery()
    local_client.publish("sensors/battery", json.dumps({"battery_state": battery["battery_state"]}), qos=0)
    print(f"[Эмуляция] power={power}, battery={battery['battery_state']}")

# ========== ОБРАБОТЧИК ЛОКАЛЬНЫХ СООБЩЕНИЙ ==========
def on_local_message(client, userdata, msg):
    topic = msg.topic
    try:
        payload = json.loads(msg.payload.decode())
        if topic.startswith("sensors/temperature/"):
            sensor_id = topic.split("/")[-1]
            hdw = payload.get("hdw_temperature")
            out = payload.get("out_temperature")
            if hdw is not None and out is not None:
                latest_data["temperatures"][sensor_id] = f"HDW:{hdw}, OUT:{out}"
                last_data_time["temperature"] = time.time()
                remote = safe_remote_client()
                if DEVICE_ID is not None and remote is not None:
                    check_alerts(remote)
        elif topic == "sensors/distance":
            dist = payload.get("distance_mm")
            if dist is not None:
                latest_data["distance"] = dist
                last_data_time["distance"] = time.time()
        elif topic == "sensors/wind":
            speed = payload.get("wind_speed_mps")
            if speed is not None:
                latest_data["wind"]["wind_speed_mps"] = speed
                last_data_time["wind"] = time.time()
                remote = safe_remote_client()
                if DEVICE_ID is not None and remote is not None:
                    check_alerts(remote)
        elif topic == "sensors/tilt":
            angle = payload.get("tilt_degrees")
            if angle is not None:
                latest_data["tilt"]["tilt_degrees"] = angle
                last_data_time["tilt"] = time.time()
                remote = safe_remote_client()
                if DEVICE_ID is not None and remote is not None:
                    check_alerts(remote)
        elif topic == "sensors/power":
            phases = payload.get("phases")
            if phases:
                latest_data["power_phases"] = phases
                last_data_time["power"] = time.time()
                remote = safe_remote_client()
                if DEVICE_ID is not None and remote is not None:
                    check_alerts(remote)
        elif topic == "sensors/battery":
            state = payload.get("battery_state")
            if state is not None:
                latest_data["battery"]["battery_state"] = state
                last_data_time["battery"] = time.time()
                remote = safe_remote_client()
                if DEVICE_ID is not None and remote is not None:
                    check_alerts(remote)
    except Exception as e:
        print(f"Ошибка локального обработчика {topic}: {e}")

# ========== ОБРАБОТЧИК УДАЛЁННЫХ СООБЩЕНИЙ ==========
def on_remote_message(client, userdata, msg):
    global DEVICE_ID, current_temp_uid
    topic = msg.topic
    try:
        payload = json.loads(msg.payload.decode())
        # Режим регистрации: устройство ещё не имеет постоянного ID
        if DEVICE_ID is None and current_temp_uid is not None:
            # Команда может прийти в ams-reg/{temp_uid}/command или в общий ams-reg/command
            if topic == f"ams-reg/{current_temp_uid}/command":
                cmd = payload.get("command")
                if cmd == "set_uid":
                    new_uid = payload.get("uid")
                    if new_uid and isinstance(new_uid, str):
                        save_uid(new_uid)
                        print(f"[РЕГИСТРАЦИЯ] Получен новый UID: {new_uid}. Перезагрузка...")
                        time.sleep(1)
                        os.system("sudo reboot")
                    else:
                        print("[РЕГИСТРАЦИЯ] Ошибка: неверный формат UID в команде")
        # Обычный режим: обрабатываем команды для зарегистрированного устройства
        elif DEVICE_ID is not None and topic == TOPIC_COMMAND:
            cmd = payload.get("command")
            if cmd == "calibrate_tilt":
                tilt_value = latest_data["tilt"].get("tilt_degrees")
                if tilt_value is not None:
                    os.makedirs(SETTINGS_DIR, exist_ok=True)
                    calib_file = os.path.join(SETTINGS_DIR, "tilt_calib.json")
                    with open(calib_file, "w") as f:
                        json.dump({"offset": tilt_value}, f, indent=2)
                    print(f"[КОМАНДА] Калибровка наклона: смещение {tilt_value}° сохранено")
                else:
                    print("[КОМАНДА] Ошибка: нет данных о наклоне")
    except Exception as e:
        print(f"Ошибка удалённого обработчика: {e}")

# ========== ПЕРЕПОДКЛЮЧЕНИЕ К УДАЛЁННОМУ БРОКЕРУ ==========
def on_remote_disconnect(client, userdata, rc):
    """Callback при потере соединения с удалённым брокером."""
    print(f"[MQTT] Удалённый брокер отключён (код: {rc}). Попытка переподключения...")

def connect_remote_with_retry():
    """Подключается к удалённому брокеру с бесконечными повторами."""
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = on_remote_message
    client.on_disconnect = on_remote_disconnect
    while not shutdown_flag:
        try:
            client.username_pw_set(REMOTE_USER, REMOTE_PASS)
            client.connect(REMOTE_BROKER, REMOTE_PORT)
            client.loop_start()
            print("Удалённый MQTT брокер подключён")
            return client
        except Exception as e:
            print(f"Ошибка подключения к удалённому брокеру: {e}, повтор через 5 сек")
            time.sleep(5)
    return None

# ========== РЕГИСТРАЦИОННЫЙ ЦИКЛ ==========
def registration_loop(remote_client):
    global current_temp_uid
    current_temp_uid = generate_temp_uid()
    print(f"[РЕГИСТРАЦИЯ] Временный UID: {current_temp_uid}")
    reg_topic_telemetry = f"ams-reg/{current_temp_uid}/telemetry"
    reg_topic_command = f"ams-reg/{current_temp_uid}/command"
    
    remote_client.subscribe(reg_topic_command)
    print(f"[РЕГИСТРАЦИЯ] Подписан на {reg_topic_command}")
    
    last_publish = 0
    registration_start = time.time()
    
    while not shutdown_flag:
        now = time.time()
        
        # Таймаут регистрации — если за REGISTRATION_TIMEOUT не получили UID, перезапускаемся
        if now - registration_start > REGISTRATION_TIMEOUT:
            print(f"[РЕГИСТРАЦИЯ] Таймаут {REGISTRATION_TIMEOUT} сек истёк. Перезапуск агрегатора...")
            time.sleep(1)
            os.execv(sys.executable, [sys.executable] + sys.argv)
        
        if now - last_publish >= 60:
            payload = json.dumps({"uid": current_temp_uid, "status": "registering"})
            remote_client.publish(reg_topic_telemetry, payload, qos=1)
            print(f"[РЕГИСТРАЦИЯ] Опубликовано в {reg_topic_telemetry}")
            last_publish = now
        
        # Проверяем, не появился ли постоянный UID (если команда set_uid уже была обработана)
        uid = load_or_create_uid()
        if uid is not None:
            set_device_id(uid)
            print(f"[РЕГИСТРАЦИЯ] Получен постоянный UID: {uid}. Перезагрузка агрегатора...")
            time.sleep(1)
            os.execv(sys.executable, [sys.executable] + sys.argv)
        
        time.sleep(2)

# ========== ОТПРАВКА АГРЕГИРОВАННЫХ ДАННЫХ ==========
def publish_with_retry(client, topic, payload, max_retries=3):
    for attempt in range(max_retries):
        try:
            client.publish(topic, payload, qos=1)
            return True
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"Ошибка публикации, повтор {attempt+2}/{max_retries}: {e}")
                time.sleep(2)
            else:
                print(f"Не удалось опубликовать после {max_retries} попыток: {e}")
    return False

def send_aggregated():
    if DEVICE_ID is None:
        return
    remote = safe_remote_client()
    if remote is None or not remote.is_connected():
        print("[ОТПРАВКА] Удалённый брокер недоступен, пропускаем отправку")
        return
    payload = {
        "device_id": DEVICE_ID,
        "fw_version": PROJECT_VERSION,
        "temperatures": latest_data["temperatures"].copy(),
        "power_phases": latest_data["power_phases"].copy(),
        "battery": latest_data["battery"].copy(),
        "wind": latest_data["wind"].copy(),
        "tilt": latest_data["tilt"].copy()
    }
    if latest_data["distance"] is not None:
        payload["distance_mm"] = latest_data["distance"]
    if not payload["temperatures"]:
        del payload["temperatures"]
    for key in ["power_phases", "battery", "wind", "tilt"]:
        if not payload[key] or all(v is None for v in payload[key].values()):
            del payload[key]
    payload_json = json.dumps(payload)
    if publish_with_retry(remote, TOPIC_REMOTE_TELEMETRY, payload_json):
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Отправлено: {len(payload.get('temperatures', {}))} темп, dist={payload.get('distance_mm')}, ветер={payload.get('wind', {}).get('wind_speed_mps')}, наклон={payload.get('tilt', {}).get('tilt_degrees')}")

def remote_loop():
    while not shutdown_flag:
        send_aggregated()
        # Ждём с проверкой shutdown_flag каждую секунду
        for _ in range(INTERVAL):
            if shutdown_flag:
                return
            time.sleep(1)

def emulation_loop(local_client):
    while not shutdown_flag and EMULATION_ENABLED:
        publish_emulated_sensors(local_client)
        # Ждём с проверкой shutdown_flag каждую секунду
        for _ in range(30):
            if shutdown_flag:
                return
            time.sleep(1)

# ========== ОБРАБОТКА ЗАВЕРШЕНИЯ ==========
def signal_handler(signum, frame):
    global shutdown_flag
    print(f"\n[ЗАВЕРШЕНИЕ] Получен сигнал {signum}. Завершаем работу...")
    shutdown_flag = True

# ========== MAIN ==========
if __name__ == "__main__":
    # Устанавливаем обработчики сигналов для graceful shutdown
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Локальный брокер (для датчиков)
    local_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    local_client.on_message = on_local_message
    while not shutdown_flag:
        try:
            local_client.username_pw_set(LOCAL_USER, LOCAL_PASS)
            local_client.connect(LOCAL_BROKER, LOCAL_PORT)
            local_client.subscribe("sensors/#")
            local_client.loop_start()
            print("Локальный MQTT брокер подключён")
            break
        except Exception as e:
            print(f"Ошибка подключения к локальному брокеру: {e}, повтор через 5 сек")
            time.sleep(5)
    
    if shutdown_flag:
        sys.exit(0)
    
    # Удалённый брокер
    remote_client = connect_remote_with_retry()
    
    if shutdown_flag:
        sys.exit(0)
    
    # Проверяем наличие постоянного UID
    permanent_uid = load_or_create_uid()
    if permanent_uid is None:
        print("Постоянный UID не найден. Запуск регистрации...")
        registration_loop(remote_client)
    else:
        set_device_id(permanent_uid)
        print(f"Устройство зарегистрировано как {DEVICE_ID}. Запуск основного цикла.")
    
    if shutdown_flag:
        sys.exit(0)
    
    # Инициализация
    prev_power_state["phases"] = latest_data["power_phases"].copy()
    prev_power_state["battery"] = latest_data["battery"]["battery_state"]
    
    # Запуск watchdog
    watchdog_thread = threading.Thread(target=watchdog_loop, daemon=True)
    watchdog_thread.start()
    print("Watchdog активен (проверка каждые 60 сек)")
    
    if EMULATION_ENABLED:
        emu_thread = threading.Thread(target=emulation_loop, args=(local_client,), daemon=True)
        emu_thread.start()
        print("Эмуляция фазы/батареи активна (каждые 30 с)")
    
    remote_loop()
    print("[ЗАВЕРШЕНИЕ] Агрегатор остановлен.")
