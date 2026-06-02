#!/usr/bin/env python3
"""
i2c_helpers.py — общие утилиты для стабильной работы с I2C на Raspberry Pi.

Содержит:
- create_i2c_bus() — создание busio.I2C с повторными попытками и встряской шины
- i2c_bus_reset() — "встряска" шины через i2cdetect
- i2c_recover() — принудительный сброс залипших устройств
- ensure_i2c_ready() — проверка готовности шины и устройства
"""

import subprocess
import time
import os
import board
import busio

# ========== НАСТРОЙКИ ==========
I2C_BUS_NUM = 1
I2CDETECT_CMD = ["i2cdetect", "-y", str(I2C_BUS_NUM)]
I2CGET_CMD = ["i2cget", "-y", str(I2C_BUS_NUM)]


def i2c_bus_reset():
    """
    "Встряска" I2C-шины через i2cdetect.
    i2cdetect отправляет пакеты на все адреса, что "будит" шину
    и сбрасывает залипшие устройства.
    
    Возвращает True, если шина отвечает (хотя бы одно устройство найдено),
    False — если шина полностью мертва.
    """
    try:
        result = subprocess.run(
            I2CDETECT_CMD,
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            # Проверяем, есть ли хоть какие-то устройства на шине
            lines = result.stdout.strip().split('\n')
            for line in lines[1:]:  # пропускаем заголовок
                parts = line.split()
                for cell in parts[1:]:  # пропускаем номер строки
                    if cell not in ('--', '00'):
                        return True
            # Устройств нет, но шина отвечает
            return True
        return False
    except subprocess.TimeoutExpired:
        print("[I2C] i2cdetect timeout — шина не отвечает")
        return False
    except FileNotFoundError:
        print("[I2C] i2cdetect не найден. Установите i2c-tools")
        return False
    except Exception as e:
        print(f"[I2C] Ошибка i2cdetect: {e}")
        return False


def i2c_recover():
    """
    Принудительный сброс I2C-шины.
    Пытается "расшевелить" шину через многократный i2cdetect.
    Если не помогает — пытается через GPIO recovery (если доступно).
    
    Возвращает True после успешного восстановления.
    """
    print("[I2C] Попытка восстановления шины...")
    
    # Попытка 1: многократный i2cdetect (до 5 раз)
    for attempt in range(1, 6):
        print(f"[I2C] Встряска шины (попытка {attempt}/5)...")
        if i2c_bus_reset():
            print("[I2C] Шина восстановлена после встряски")
            return True
        time.sleep(1)
    
    # Попытка 2: если есть i2c-gpio recovery через Device Tree
    recovery_path = f"/sys/bus/i2c/devices/i2c-{I2C_BUS_NUM}/recover"
    if os.path.exists(recovery_path):
        try:
            with open(recovery_path, 'w') as f:
                f.write('1')
            print("[I2C] Recovery через sysfs выполнен")
            time.sleep(0.5)
            if i2c_bus_reset():
                print("[I2C] Шина восстановлена после sysfs recovery")
                return True
        except Exception as e:
            print(f"[I2C] sysfs recovery не сработал: {e}")
    
    # Попытка 3: перезагрузка модулей i2c-dev и i2c-bcm2835
    print("[I2C] Попытка перезагрузки модулей I2C...")
    try:
        subprocess.run(["modprobe", "-r", "i2c-bcm2835"], capture_output=True, timeout=5)
        subprocess.run(["modprobe", "-r", "i2c-dev"], capture_output=True, timeout=5)
        time.sleep(0.5)
        subprocess.run(["modprobe", "i2c-dev"], capture_output=True, timeout=5)
        subprocess.run(["modprobe", "i2c-bcm2835"], capture_output=True, timeout=5)
        time.sleep(1)
        if i2c_bus_reset():
            print("[I2C] Шина восстановлена после перезагрузки модулей")
            return True
    except Exception as e:
        print(f"[I2C] Перезагрузка модулей не удалась: {e}")
    
    print("[I2C] НЕ УДАЛОСЬ восстановить шину")
    return False


def i2c_check_device(addr):
    """
    Проверяет наличие устройства на указанном I2C-адресе через i2cget.
    Использует однобайтовое чтение регистра 0x00 (без флага 'w') —
    это более надёжно для простой проверки наличия чипа на шине.
    
    addr: целочисленный адрес устройства (например, 0x48)
    Возвращает True, если устройство отвечает.
    """
    addr_str = f"0x{addr:02X}"
    try:
        result = subprocess.run(
            I2CGET_CMD + [addr_str, "0x00"],
            capture_output=True, text=True, timeout=3
        )
        return result.returncode == 0
    except Exception:
        return False


def create_i2c_bus(max_retries=5, retry_delay=1):
    """
    Создаёт шину busio.I2C с повторными попытками и предварительной
    встряской/восстановлением шины через i2c-tools.
    
    max_retries: максимальное количество попыток создания шины
    retry_delay: задержка между попытками (сек)
    
    Возвращает объект busio.I2C или None при невозможности создать.
    """
    for attempt in range(1, max_retries + 1):
        try:
            # Встряска шины перед созданием
            i2c_bus_reset()
            time.sleep(0.2)
            
            bus = busio.I2C(board.SCL, board.SDA)
            # Пробуем просканировать шину через созданный bus
            bus.try_lock()
            try:
                devices = bus.scan()
                if devices:
                    print(f"[I2C] Шина создана успешно, найдены устройства: {[hex(d) for d in devices]}")
                    return bus
                print(f"[I2C] Шина создана, но устройства не обнаружены (попытка {attempt}/{max_retries})")
            finally:
                bus.unlock()
            
            # Если устройств нет — пробуем ещё раз
            if attempt < max_retries:
                delay = retry_delay * (2 ** (attempt - 1))
                print(f"[I2C] Повтор создания шины через {delay} сек...")
                time.sleep(delay)
                
        except Exception as e:
            print(f"[I2C] Ошибка создания шины busio (попытка {attempt}/{max_retries}): {e}")
            if attempt < max_retries:
                delay = retry_delay * (2 ** (attempt - 1))
                print(f"[I2C] Повтор через {delay} сек...")
                time.sleep(delay)
                i2c_recover()
    
    print("[I2C] Не удалось создать шину busio после всех попыток")
    return None


def ensure_i2c_ready(addr=None, max_retries=3, delay=1):
    """
    Проверяет, что I2C-шина готова к работе.
    Если шина не отвечает — пытается восстановить.
    
    addr: если указан, проверяет конкретное устройство через i2cget
    max_retries: максимальное количество попыток восстановления
    delay: задержка между попытками (сек)
    
    Возвращает True, если шина (и устройство) доступны.
    """
    for attempt in range(1, max_retries + 1):
        # Сначала встряхиваем шину
        if not i2c_bus_reset():
            print(f"[I2C] Шина не отвечает (попытка {attempt}/{max_retries})")
            i2c_recover()
            time.sleep(delay)
            continue
        
        # Если указан конкретный адрес — проверяем его
        if addr is not None:
            if i2c_check_device(addr):
                return True
            print(f"[I2C] Устройство 0x{addr:02X} не отвечает (попытка {attempt}/{max_retries})")
            i2c_recover()
            time.sleep(delay)
        else:
            return True
    
    return False