#!/usr/bin/env python3 -u
import time
import json
import sys
import signal
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

# Watchdog: максимальное время между публикациями (сек)
PUBLISH_INTERVAL = 1
MAX_SKIP_BEFORE_RESTART = 10  # если 10 раз подряд ошибка — перезапускаем I2C

shutdown_flag = False

def mv_to_wind_speed(mv):
    if mv <= 0:
        return 0.0
    if mv >= VOLTAGE_MAX_MV:
        return WIND_MAX_MPS
    return (mv / VOLTAGE_MAX_MV) * WIND_MAX_MPS

def connect_mqtt():
    """Подключается к MQTT с бесконечными повторами."""
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    while not shutdown_flag:
        try:
            client.connect(MQTT_BROKER, MQTT_PORT)
            client.loop_start()
            print("INA219: MQTT подключён")
            return client
        except Exception as e:
            print(f"INA219: Ошибка подключения к MQTT: {e}, повтор через 5 сек...")
            time.sleep(5)
    return None

def init_ina():
    """Инициализирует INA219 с повторными попытками."""
    while not shutdown_flag:
        try:
            ina = INA219(SHUNT_OHMS, busnum=1)
            ina.configure(voltage_range=ina.RANGE_32V, gain=ina.GAIN_AUTO,
                          bus_adc=ina.ADC_12BIT, shunt_adc=ina.ADC_12BIT)
            print("INA219: датчик инициализирован")
            return ina
        except Exception as e:
            print(f"INA219: Ошибка инициализации: {e}, повтор через 3 сек...")
            time.sleep(3)
    return None

def signal_handler(signum, frame):
    global shutdown_flag
    print(f"\n[INA219] Получен сигнал {signum}. Завершаем работу...")
    shutdown_flag = True

# ========== MAIN ==========
if __name__ == "__main__":
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    print("Wind worker (INA219): измеряем напряжение с аналогового датчика ветра (динамо)")
    
    client = connect_mqtt()
    if client is None:
        sys.exit(1)
    
    ina = init_ina()
    if ina is None:
        sys.exit(1)
    
    error_count = 0
    
    while not shutdown_flag:
        try:
            # Проверка соединения с MQTT
            if not client.is_connected():
                print("INA219: MQTT разорван, переподключаемся...")
                client = connect_mqtt()
                if client is None:
                    break
                continue
            
            # Читаем дифференциальное напряжение (Vin+ - Vin-)
            voltage_v = ina.voltage()
            voltage_mv = voltage_v * 1000

            if voltage_mv < 0:
                voltage_mv = 0

            wind_speed = mv_to_wind_speed(voltage_mv)

            payload = {"wind_speed_mps": round(wind_speed, 1)}
            client.publish(MQTT_TOPIC_WIND, json.dumps(payload), qos=0)

            print(f"[ВЕТЕР INA219] Напряжение: {voltage_mv:.0f} мВ -> {wind_speed:.1f} м/с")
            
            error_count = 0  # сброс счётчика ошибок после успешного чтения
            
            # Ожидание с проверкой shutdown_flag
            for _ in range(PUBLISH_INTERVAL):
                if shutdown_flag:
                    break
                time.sleep(1)

        except DeviceRangeError as e:
            print(f"INA219: Ошибка диапазона: {e}")
            error_count += 1
            time.sleep(1)
        except (OSError, IOError) as e:
            print(f"INA219: Ошибка I2C: {e}. Переинициализация...")
            error_count += 1
            try:
                ina = init_ina()
            except:
                pass
            time.sleep(2)
        except Exception as e:
            print(f"INA219: Неизвестная ошибка: {e}")
            error_count += 1
            time.sleep(1)
        
        # Если слишком много ошибок подряд — перезапускаем I2C
        if error_count >= MAX_SKIP_BEFORE_RESTART:
            print(f"INA219: {error_count} ошибок подряд. Полная переинициализация I2C...")
            try:
                ina = init_ina()
            except:
                pass
            error_count = 0
            time.sleep(3)
    
    print("[INA219] Завершён.")
