#!/bin/bash
# ============================================================
# sync_timezone.sh — Синхронизация часового пояса устройства
# с реальным местоположением через IP-геолокацию
# Использует бесплатный API ip-api.com (без ключа, 45 запр/мин)
# ============================================================

set -e

# URL API для определения часового пояса по IP
API_URL="http://ip-api.com/json/?fields=timezone"

echo "[TZ-SYNC] Определение часового пояса по IP..."

# Выполняем запрос (таймаут 5 сек на соединение, 10 сек на ответ)
RESPONSE=$(curl -s --connect-timeout 5 --max-time 10 "$API_URL" 2>&1) || {
    echo "[TZ-SYNC] ОШИБКА: не удалось выполнить запрос к ip-api.com"
    echo "[TZ-SYNC] Проверьте интернет-соединение"
    exit 0
}

# Парсим timezone из JSON-ответа (используем grep + sed, чтобы не требовать jq)
TZ=$(echo "$RESPONSE" | grep -oP '"timezone"\s*:\s*"\K[^"]+')

if [ -z "$TZ" ]; then
    echo "[TZ-SYNC] ОШИБКА: не удалось определить часовой пояс из ответа: $RESPONSE"
    exit 0
fi

echo "[TZ-SYNC] Определён часовой пояс: $TZ"

# Получаем текущий часовой пояс
CURRENT_TZ=$(timedatectl show --property=Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || echo "unknown")

echo "[TZ-SYNC] Текущий часовой пояс: $CURRENT_TZ"

if [ "$TZ" = "$CURRENT_TZ" ]; then
    echo "[TZ-SYNC] Часовой пояс актуален, изменений не требуется"
    exit 0
fi

# Устанавливаем новый часовой пояс
echo "[TZ-SYNC] Установка часового пояса: $TZ"
if [ "$EUID" -eq 0 ]; then
    timedatectl set-timezone "$TZ" 2>&1 || {
        echo "[TZ-SYNC] ОШИБКА: не удалось установить часовой пояс через timedatectl"
        # Запасной вариант: запись в /etc/timezone
        echo "$TZ" > /etc/timezone 2>/dev/null || true
        # Пересоздаём симлинк localtime
        if [ -f "/usr/share/zoneinfo/$TZ" ]; then
            ln -sf "/usr/share/zoneinfo/$TZ" /etc/localtime 2>/dev/null || true
            echo "[TZ-SYNC] Часовой пояс установлен через /etc/localtime"
        fi
    }
else
    echo "[TZ-SYNC] Нет прав root для установки часового пояса"
    echo "[TZ-SYNC] Запустите: sudo timedatectl set-timezone $TZ"
    exit 0
fi

echo "[TZ-SYNC] Часовой пояс успешно изменён: $CURRENT_TZ -> $TZ"