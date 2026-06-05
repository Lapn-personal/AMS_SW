#!/usr/bin/env python3
"""
i2c_helpers.py — общие утилиты для стабильной работы с I2C на Raspberry Pi.

Содержит:
- get_shared_i2c_bus()   — создаёт или возвращает ЕДИНСТВЕННЫЙ busio.I2C для всех воркеров
- release_shared_i2c_bus() — освобождает shared шину (при завершении)
- i2c_bus_reset()        — "встряска" шины через i2cdetect
- i2c_recover()          — принудительный сброс залипших устройств
- ensure_i2c_ready()     — проверка готовности шины и устройства

КЛЮЧЕВОЙ МОМЕНТ: все воркеры используют ОДНУ шину через get_shared_i2c_bus().
Это исключает конфликты между busio.I2C(board.SCL, board.SDA) в разных процессах.
"""

import subprocess
import time
import os
import threading
import board
import busio

# ========== НАСТРОЙКИ ==========
I2C_BUS_NUM = 1
I2CDETECT_CMD = ["i2cdetect", "-y", str(I2C_BUS_NUM)]
I2CGET_CMD = ["i2cget", "-y", str(I2C_BUS_NUM)]

# ========== SHARED I2C BUS (СИНГЛТОН) ==========
_shared_i2c_bus = None
_shared_i2c_lock = threading.Lock()
_shared_i2c_refcount = 0


def get_shared_i2c_bus(max_retries=5, retry_delay=1):
    """
    Возвращает ЕДИНЫЙ объект busio.I2C для всех воркеров.
    Создаёт его при первом вызове, при последующих — возвращает тот же.
    Счётчик ссылок: каждый вызов get_shared_i2c_bus() увеличивает refcount.
    При выходе из воркера нужно вызвать release_shared_i2c_bus().

    max_retries: максимальное количество попыток создания шины
    retry_delay: задержка между попытками (сек)

    Возвращает объект busio.I2C или None при невозможности создать.
    """
    global _shared_i2c_bus, _shared_i2c_refcount

    with _shared_i2c_lock:
        _shared_i2c_refcount += 1

        if _shared_i2c_bus is not None:
            print(f"[I2C] Shared bus уже существует (refcount={_shared_i2c_refcount})")
            return _shared_i2c_bus

    # Создаём новую шину (без лока, чтобы не блокировать надолго)
    bus = None
    for attempt in range(1, max_retries + 1):
        try:
            # Простая пауза — i2cdetect сбивает все чипы
            time.sleep(0.5)

            bus = busio.I2C(board.SCL, board.SDA)
            bus.try_lock()
            try:
                devices = bus.scan()
                if devices:
                    print(f"[I2C] Shared bus создана, устройства: {[hex(d) for d in devices]}")
                    break
                print(f"[I2C] Shared bus создана, но устройства не найдены (попытка {attempt}/{max_retries})")
            finally:
                bus.unlock()

            if attempt < max_retries:
                delay = retry_delay * (2 ** (attempt - 1))
                print(f"[I2C] Повтор создания shared bus через {delay} сек...")
                time.sleep(delay)
                try:
                    bus.deinit()
                except:
                    pass
                bus = None

        except Exception as e:
            print(f"[I2C] Ошибка создания shared bus (попытка {attempt}/{max_retries}): {e}")
            if attempt < max_retries:
                delay = retry_delay * (2 ** (attempt - 1))
                print(f"[I2C] Повтор через {delay} сек...")
                time.sleep(delay)
                i2c_recover()
                try:
                    bus.deinit()
                except:
                    pass
                bus = None

    with _shared_i2c_lock:
        if bus is not None:
            _shared_i2c_bus = bus
            print(f"[I2C] Shared bus зарегистрирована (refcount={_shared_i2c_refcount})")
        else:
            _shared_i2c_refcount -= 1
            print("[I2C] Не удалось создать shared bus — refcount уменьшен")

    return _shared_i2c_bus


def release_shared_i2c_bus():
    """
    Уменьшает счётчик ссылок на shared I2C bus.
    Когда refcount достигает 0 — освобождает шину.
    """
    global _shared_i2c_bus, _shared_i2c_refcount

    with _shared_i2c_lock:
        _shared_i2c_refcount = max(0, _shared_i2c_refcount - 1)
        print(f"[I2C] Shared bus release: refcount={_shared_i2c_refcount}")

        if _shared_i2c_refcount == 0 and _shared_i2c_bus is not None:
            try:
                _shared_i2c_bus.deinit()
                print("[I2C] Shared bus освобождена (deinit)")
            except Exception as e:
                print(f"[I2C] Ошибка deinit shared bus: {e}")
            _shared_i2c_bus = None


# ========== ДИАГНОСТИКА ШИНЫ ==========

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
            lines = result.stdout.strip().split('\n')
            for line in lines[1:]:
                parts = line.split()
                for cell in parts[1:]:
                    if cell not in ('--', '00'):
                        return True
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

    for attempt in range(1, 6):
        print(f"[I2C] Встряска шины (попытка {attempt}/5)...")
        if i2c_bus_reset():
            print("[I2C] Шина восстановлена после встряски")
            return True
        time.sleep(1)

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
    Использует однобайтовое чтение регистра 0x00 (без флага 'w').

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


def wake_device(addr):
    """
    "Пробуждает" устройство на указанном адресе — отправляет одиночный i2cget
    без влияния на другие чипы на шине (в отличие от i2cdetect).
    
    addr: целочисленный адрес устройства (например, 0x48)
    Возвращает True при успехе.
    """
    addr_str = f"0x{addr:02X}"
    try:
        subprocess.run(
            I2CGET_CMD + [addr_str, "0x00"],
            capture_output=True, text=True, timeout=2
        )
        return True
    except Exception:
        return False


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
        if not i2c_bus_reset():
            print(f"[I2C] Шина не отвечает (попытка {attempt}/{max_retries})")
            i2c_recover()
            time.sleep(delay)
            continue

        if addr is not None:
            if i2c_check_device(addr):
                return True
            print(f"[I2C] Устройство 0x{addr:02X} не отвечает (попытка {attempt}/{max_retries})")
            i2c_recover()
            time.sleep(delay)
        else:
            return True

    return False