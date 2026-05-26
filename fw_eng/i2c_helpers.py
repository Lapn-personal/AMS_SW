#!/usr/bin/env python3
"""
i2c_helpers.py — общие утилиты для стабильной работы с I2C на Raspberry Pi.

Содержит:
- i2c_bus_reset() — "встряска" шины через i2cdetect
- i2c_recover() — принудительный сброс залипших устройств
- retry_i2c() — декоратор/обёртка с экспоненциальной задержкой
"""

import subprocess
import time
import os
import sys

# ========== НАСТРОЙКИ ==========
I2C_BUS = 1
I2CDETECT_CMD = ["i2cdetect", "-y", str(I2C_BUS)]
I2CGET_CMD = ["i2cget", "-y", str(I2C_BUS)]
I2CSET_CMD = ["i2cset", "-y", str(I2C_BUS)]


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
    # Пробуем записать в sysfs, если доступно
    recovery_path = f"/sys/bus/i2c/devices/i2c-{I2C_BUS}/recover"
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


def i2c_read_word(addr, reg, max_retries=3, delay=0.5):
    """
    Читает 16-битное значение из регистра I2C-устройства через i2cget.
    
    addr: адрес устройства (hex, например 0x48)
    reg: регистр (hex, например 0x00)
    max_retries: макс. количество попыток
    delay: задержка между попытками (сек)
    
    Возвращает int или None при ошибке.
    """
    addr_str = f"0x{addr:02X}"
    reg_str = f"0x{reg:02X}"
    
    for attempt in range(1, max_retries + 1):
        try:
            result = subprocess.run(
                I2CGET_CMD + [addr_str, reg_str, 'w'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                val = int(result.stdout.strip(), 16)
                # i2cget возвращает little-endian для word, меняем байты
                return ((val & 0xFF) << 8) | ((val >> 8) & 0xFF)
            else:
                if attempt < max_retries:
                    time.sleep(delay)
        except Exception as e:
            if attempt < max_retries:
                print(f"[I2C] Ошибка чтения {addr_str}[{reg_str}] (попытка {attempt}): {e}")
                time.sleep(delay)
    return None


def i2c_write_word(addr, reg, value, max_retries=3, delay=0.5):
    """
    Записывает 16-битное значение в регистр I2C-устройства через i2cset.
    
    addr: адрес устройства (hex, например 0x48)
    reg: регистр (hex, например 0x01)
    value: значение для записи (int)
    max_retries: макс. количество попыток
    delay: задержка между попытками (сек)
    
    Возвращает True при успехе.
    """
    addr_str = f"0x{addr:02X}"
    reg_str = f"0x{reg:02X}"
    # Меняем байты местами для i2cset (little-endian)
    swapped = ((value & 0xFF) << 8) | ((value >> 8) & 0xFF)
    val_str = f"0x{swapped:04X}"
    
    for attempt in range(1, max_retries + 1):
        try:
            result = subprocess.run(
                I2CSET_CMD + [addr_str, reg_str, val_str, 'w'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return True
            else:
                if attempt < max_retries:
                    time.sleep(delay)
        except Exception as e:
            if attempt < max_retries:
                print(f"[I2C] Ошибка записи {addr_str}[{reg_str}] (попытка {attempt}): {e}")
                time.sleep(delay)
    return False


def i2c_write_block(addr, reg, data_bytes, max_retries=3, delay=0.5):
    """
    Записывает блок байт в I2C-устройство через i2cset с режимом i2c block.
    
    addr: адрес устройства (hex, например 0x48)
    reg: регистр (hex, например 0x01)
    data_bytes: список байт для записи (например [0x83, 0x86])
    max_retries: макс. количество попыток
    delay: задержка между попытками (сек)
    
    Возвращает True при успехе.
    """
    addr_str = f"0x{addr:02X}"
    reg_str = f"0x{reg:02X}"
    
    for attempt in range(1, max_retries + 1):
        try:
            cmd = I2CSET_CMD + [addr_str, reg_str] + [f"0x{b:02X}" for b in data_bytes] + ['i']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                return True
            else:
                if attempt < max_retries:
                    time.sleep(delay)
        except Exception as e:
            if attempt < max_retries:
                print(f"[I2C] Ошибка block-записи {addr_str}[{reg_str}] (попытка {attempt}): {e}")
                time.sleep(delay)
    return False


def i2c_read_block(addr, reg, length, max_retries=3, delay=0.5):
    """
    Читает блок байт из I2C-устройства через i2cget с режимом i2c block.
    
    addr: адрес устройства (hex, например 0x68)
    reg: регистр (hex, например 0x3B)
    length: количество байт для чтения
    max_retries: макс. количество попыток
    delay: задержка между попытками (сек)
    
    Возвращает список байт или None при ошибке.
    """
    addr_str = f"0x{addr:02X}"
    reg_str = f"0x{reg:02X}"
    
    for attempt in range(1, max_retries + 1):
        try:
            result = subprocess.run(
                I2CGET_CMD + [addr_str, reg_str, 'i', str(length)],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                # Парсим вывод: "0x3B 0x00 0x00 0x00 ..."
                parts = result.stdout.strip().split()
                return [int(p, 16) for p in parts]
            else:
                if attempt < max_retries:
                    time.sleep(delay)
        except Exception as e:
            if attempt < max_retries:
                print(f"[I2C] Ошибка block-чтения {addr_str}[{reg_str}] (попытка {attempt}): {e}")
                time.sleep(delay)
    return None


def ensure_i2c_ready(addr=None, max_retries=3, delay=1):
    """
    Проверяет, что I2C-шина готова к работе.
    Если шина не отвечает — пытается восстановить.
    
    addr: если указан, проверяет конкретное устройство
    max_retries: макс. количество попыток восстановления
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
            val = i2c_read_word(addr, 0x00, max_retries=1)
            if val is not None:
                return True
            print(f"[I2C] Устройство 0x{addr:02X} не отвечает (попытка {attempt}/{max_retries})")
            i2c_recover()
            time.sleep(delay)
        else:
            return True
    
    return False
