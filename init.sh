#!/bin/bash
# ============================================================
# init.sh — Инициализация ПО при запуске устройства
# Проверяет/создаёт виртуальное окружение, обновляет библиотеки,
# затем запускает IOT_main_start.sh
# ============================================================

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$PROJECT_DIR/fw_env"
REQUIREMENTS="$PROJECT_DIR/requirements.txt"
VERSION_FILE="$PROJECT_DIR/VERSION"

# Читаем версию проекта
if [ -f "$VERSION_FILE" ]; then
    PROJECT_VERSION=$(cat "$VERSION_FILE" | tr -d ' \n\r\t')
else
    PROJECT_VERSION="unknown"
fi

echo "[INIT] AMS_SW v$PROJECT_VERSION"
echo "[INIT] Проект: $PROJECT_DIR"

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

# 3. Обновляем pip
echo "[INIT] Обновление pip..."
pip install --upgrade pip --quiet

# 4. Устанавливаем/обновляем зависимости из requirements.txt
if [ -f "$REQUIREMENTS" ]; then
    echo "[INIT] Проверка и обновление Python-библиотек..."
    pip install --upgrade -r "$REQUIREMENTS" --quiet
    if [ $? -eq 0 ]; then
        echo "[INIT] Библиотеки обновлены"
    else
        echo "[INIT] ОШИБКА при обновлении библиотек"
    fi
else
    echo "[INIT] Файл requirements.txt не найден, пропускаем обновление библиотек"
fi

# 5. Запускаем основную программу
echo "[INIT] Запуск IOT_main_start.sh..."
cd "$PROJECT_DIR"
exec bash IOT_main_start.sh "$@"
