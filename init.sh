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

# 0. Проверка и установка правильной версии Python
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=9

check_python() {
    local cmd="$1"
    if command -v "$cmd" &> /dev/null; then
        local version=$("$cmd" --version 2>&1 | grep -oP '\d+\.\d+')
        local major="${version%%.*}"
        local minor="${version#*.}"
        minor="${minor%%.*}"
        if [ "$major" -gt "$MIN_PYTHON_MAJOR" ] || { [ "$major" -eq "$MIN_PYTHON_MAJOR" ] && [ "$minor" -ge "$MIN_PYTHON_MINOR" ]; }; then
            echo "$cmd"
            return 0
        fi
    fi
    return 1
}

PYTHON_CMD=""
for candidate in python3 python3.11 python3.10 python3.9; do
    if result=$(check_python "$candidate"); then
        PYTHON_CMD="$result"
        break
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo "[INIT] Python $MIN_PYTHON_MAJOR.$MIN_PYTHON_MINOR+ не найден. Устанавливаю..."
    if [ "$EUID" -eq 0 ]; then
        apt-get update -qq && apt-get install -y -qq python3 python3-pip python3-venv python3-dev 2>&1 || true

        # Проверяем ещё раз после установки
        for candidate in python3 python3.11 python3.10 python3.9; do
            if result=$(check_python "$candidate"); then
                PYTHON_CMD="$result"
                break
            fi
        done
        if [ -z "$PYTHON_CMD" ]; then
            echo "[INIT] ОШИБКА: не удалось установить Python $MIN_PYTHON_MAJOR.$MIN_PYTHON_MINOR+"
            exit 1
        fi
        echo "[INIT] Python установлен: $($PYTHON_CMD --version)"
    else
        echo "[INIT] Нет прав root. Установите Python вручную: sudo apt install python3 python3-pip python3-venv"
        exit 1
    fi
else
    echo "[INIT] Python найден: $($PYTHON_CMD --version)"
fi

# Проверка python3-dev (нужен для сборки C-расширений, например rpi_ws281x, RPi.GPIO)
if ! dpkg -s python3-dev &>/dev/null 2>&1; then
    echo "[INIT] python3-dev не установлен. Устанавливаю..."
    if [ "$EUID" -eq 0 ]; then
        # Обновляем индексы пакетов перед установкой
        apt-get update -qq 2>&1 || true

        # Определяем точную версию Python (например, 3.11, 3.13)
        PYTHON_FULL_VER=$($PYTHON_CMD --version 2>&1 | grep -oP '\d+\.\d+')
        DEV_PACKAGE="python${PYTHON_FULL_VER}-dev"

        echo "[INIT] Установка ${DEV_PACKAGE}..."
        apt-get install -y -qq "$DEV_PACKAGE" 2>&1 || {
            echo "[INIT] Предупреждение: не удалось установить ${DEV_PACKAGE}"
            echo "[INIT] Пробуем установить python3-dev как запасной вариант..."
            apt-get install -y -qq python3-dev 2>&1 || echo "[INIT] Предупреждение: не удалось установить python3-dev"
        }
    else
        echo "[INIT] Нет прав root для установки python3-dev"
    fi
else
    echo "[INIT] python3-dev уже установлен"
fi

# 1. Установка и настройка локального MQTT-брокера (Mosquitto)
if ! command -v mosquitto &>/dev/null 2>&1; then
    echo "[INIT] Mosquitto не установлен. Устанавливаю..."
    if [ "$EUID" -eq 0 ]; then
        apt-get install -y -qq mosquitto mosquitto-clients 2>&1 || echo "[INIT] Предупреждение: не удалось установить Mosquitto"
    else
        echo "[INIT] Нет прав root для установки Mosquitto"
    fi
fi

# Настройка Mosquitto (если установлен)
if command -v mosquitto &>/dev/null 2>&1; then
    MOSQUITTO_CONF_SRC="$PROJECT_DIR/fw_settings/mosquitto.conf"
    MOSQUITTO_CONF_DST="/etc/mosquitto/conf.d/ams_sensors.conf"
    MOSQUITTO_PASSWD_FILE="/etc/mosquitto/ams_passwd"

    # Копируем конфигурацию
    if [ -f "$MOSQUITTO_CONF_SRC" ]; then
        if [ "$EUID" -eq 0 ]; then
            cp "$MOSQUITTO_CONF_SRC" "$MOSQUITTO_CONF_DST"
            echo "[INIT] Конфигурация Mosquitto скопирована"
        fi
    fi

    # Создаём файл паролей, если его нет
    if [ ! -f "$MOSQUITTO_PASSWD_FILE" ]; then
        if [ "$EUID" -eq 0 ]; then
            if command -v mosquitto_passwd &>/dev/null; then
                mosquitto_passwd -c -b "$MOSQUITTO_PASSWD_FILE" "ams_iot" "ams_iot_pass" 2>&1 || true
                echo "[INIT] Пароль Mosquitto создан"
            fi
        fi
    fi

    # Запускаем и включаем Mosquitto
    if [ "$EUID" -eq 0 ]; then
        systemctl enable mosquitto 2>&1 || true
        systemctl restart mosquitto 2>&1 || true
        echo "[INIT] Mosquitto запущен"
    fi
else
    echo "[INIT] Mosquitto не установлен, пропускаем настройку"
fi

# 2. Автоматическая установка systemd-сервиса, если ещё не установлен
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

# 3. Создаём виртуальное окружение, если его нет
if [ ! -d "$VENV_DIR" ]; then
    echo "[INIT] Создание виртуального окружения..."
    $PYTHON_CMD -m venv "$VENV_DIR"
    if [ $? -ne 0 ]; then
        echo "[INIT] ОШИБКА: не удалось создать виртуальное окружение"
        exit 1
    fi
fi

# 4. Активируем виртуальное окружение
source "$VENV_DIR/bin/activate"
if [ $? -ne 0 ]; then
    echo "[INIT] ОШИБКА: не удалось активировать виртуальное окружение"
    exit 1
fi
echo "[INIT] Виртуальное окружение активировано"

# 5. Обновляем pip (без кэша)
echo "[INIT] Обновление pip..."
pip install --upgrade pip --quiet --no-cache-dir

# 6. Устанавливаем/обновляем зависимости из requirements.txt (без кэша)
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

# 7. Отключаем лишние systemd-службы для защиты SD-карты
echo "[INIT] Проверка и отключение лишних systemd-служб..."
if [ -f "$PROJECT_DIR/fw_eng/service_hardener.sh" ]; then
    sudo bash "$PROJECT_DIR/fw_eng/service_hardener.sh" 2>&1 || echo "[INIT] Предупреждение: service_hardener.sh завершился с ошибкой"
else
    echo "[INIT] service_hardener.sh не найден, пропускаем"
fi

# 8. Настройка I2C (если ещё не настроен)
echo "[INIT] Проверка и настройка I2C..."
if [ -f "$PROJECT_DIR/fw_eng/i2c_setup.sh" ]; then
    sudo bash "$PROJECT_DIR/fw_eng/i2c_setup.sh" 2>&1 || echo "[INIT] Предупреждение: i2c_setup.sh завершился с ошибкой"
else
    echo "[INIT] i2c_setup.sh не найден, пропускаем"
fi

# 9. Проверка I2C после настройки
echo "[INIT] Проверка I2C..."
if [ -c /dev/i2c-1 ]; then
    echo "[INIT] I2C-1 доступен"
elif [ -c /dev/i2c-20 ] || [ -c /dev/i2c-21 ]; then
    echo "[INIT] I2C-1 не найден, но обнаружены альтернативные шины (i2c-20, i2c-21)"
    echo "[INIT] Продолжаем запуск — воркеры будут использовать board.SCL/board.SDA"
else
    echo "[INIT] I2C не обнаружен!"
    echo "[INIT] Возможно, требуется перезагрузка для применения настроек из /boot/config.txt"
    echo "[INIT] Запуск воркеров отложен. После перезагрузки init.sh запустится автоматически."
    exit 0
fi

# 10. Запускаем основную программу
echo "[INIT] Запуск IOT_main_start.sh..."
cd "$PROJECT_DIR"
if [ "$1" = "--daemon" ]; then
    # При запуске из systemd — передаём --daemon, чтобы не цеплять tmux attach
    bash IOT_main_start.sh --daemon
    # Оставляем процесс в памяти, чтобы systemd видел сервис запущенным
    # и мог перезапустить при падении (Restart=always)
    echo "[INIT] Сервис запущен. Ожидание завершения..."
    exec sleep infinity
else
    exec bash IOT_main_start.sh "$@"
fi
