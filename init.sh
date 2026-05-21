#!/bin/bash
# ============================================================
# init.sh — Инициализация ПО при запуске устройства
# Проверяет/создаёт виртуальное окружение, обновляет библиотеки,
# затем запускает IOT_main_start.sh
# ============================================================

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$PROJECT_DIR/fw_env"
REQUIREMENTS="$PROJECT_DIR/requirements.txt"
VERSION_FILE="$PROJECT_DIR/fw_settings/VERSION"

# Отключаем кэш pip для защиты SD-карты от износа
export PIP_NO_CACHE_DIR=1
export PIP_CACHE_DIR=/dev/null

# Читаем версию проекта
if [ -f "$VERSION_FILE" ]; then
    PROJECT_VERSION=$(cat "$VERSION_FILE" | tr -d ' \n\r\t')
else
    PROJECT_VERSION="unknown"
fi

echo "[INIT] AMS_SW v$PROJECT_VERSION"
echo "[INIT] Проект: $PROJECT_DIR"

# 0. Автоматическая установка systemd-сервиса, если ещё не установлен
SERVICE_NAME="ams-sensors.service"
SERVICE_DST="/etc/systemd/system/$SERVICE_NAME"
SERVICE_SRC="$PROJECT_DIR/services/$SERVICE_NAME"

if [ ! -f "$SERVICE_DST" ] && [ -f "$SERVICE_SRC" ]; then
    echo "[INIT] Сервис $SERVICE_NAME не установлен. Устанавливаю..."
    if [ "$EUID" -eq 0 ]; then
        cp "$SERVICE_SRC" "$SERVICE_DST"
        systemctl daemon-reload
        systemctl enable "$SERVICE_NAME"
        systemctl start "$SERVICE_NAME"
        echo "[INIT] Сервис $SERVICE_NAME установлен и запущен"
    else
        echo "[INIT] Нет прав root. Установите вручную: sudo bash $PROJECT_DIR/services/install.sh"
    fi
else
    echo "[INIT] Сервис $SERVICE_NAME уже установлен"
fi

# 1. Создаём виртуальное окружение, если его нет

if [ ! -d "$VENV_DIR" ]; then
    echo "[INIT] Создание виртуального окружения..."
    python3 -m venv "$VENV_DIR"
    if [ $? -ne 0 ]; then
        echo "[INIT] ОШИБКА: не удалось создать виртуальное окружение"
        exit 1
    fi
fi

# 2. Активируем виртуальное окружение
source "$VENV_DIR/bin/activate"
if [ $? -ne 0 ]; then
    echo "[INIT] ОШИБКА: не удалось активировать виртуальное окружение"
    exit 1
fi
echo "[INIT] Виртуальное окружение активировано"

# 3. Обновляем pip (без кэша)
echo "[INIT] Обновление pip..."
pip install --upgrade pip --quiet --no-cache-dir

# 4. Устанавливаем/обновляем зависимости из requirements.txt (без кэша)
if [ -f "$REQUIREMENTS" ]; then
    echo "[INIT] Проверка и обновление Python-библиотек..."
    pip install --upgrade -r "$REQUIREMENTS" --quiet --no-cache-dir
    if [ $? -eq 0 ]; then
        echo "[INIT] Библиотеки обновлены"
    else
        echo "[INIT] ОШИБКА при обновлении библиотек"
    fi
else
    echo "[INIT] Файл requirements.txt не найден, пропускаем обновление библиотек"
fi

# 5. Отключаем лишние systemd-службы для защиты SD-карты
echo "[INIT] Проверка и отключение лишних systemd-служб..."
if [ -f "$PROJECT_DIR/fw_eng/service_hardener.sh" ]; then
    sudo bash "$PROJECT_DIR/fw_eng/service_hardener.sh" 2>&1 || echo "[INIT] Предупреждение: service_hardener.sh завершился с ошибкой"
else
    echo "[INIT] service_hardener.sh не найден, пропускаем"
fi

# 6. Настройка I2C (если ещё не настроен)
echo "[INIT] Проверка и настройка I2C..."
if [ -f "$PROJECT_DIR/fw_eng/i2c_setup.sh" ]; then
    sudo bash "$PROJECT_DIR/fw_eng/i2c_setup.sh" 2>&1 || echo "[INIT] Предупреждение: i2c_setup.sh завершился с ошибкой"
else
    echo "[INIT] i2c_setup.sh не найден, пропускаем"
fi

# 7. Запускаем основную программу
echo "[INIT] Запуск IOT_main_start.sh..."
cd "$PROJECT_DIR"
exec bash IOT_main_start.sh "$@"
