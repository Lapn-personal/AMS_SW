#!/bin/bash
# ============================================================
# install.sh — Установка всех systemd-сервисов AMS Sensors
# Запускать с sudo: sudo bash install.sh
# Устанавливает:
#   1. ams-sensors.service — основной сервис сбора данных
#   2. auto-update.timer       — ежедневное обновление из Git в 3:00
# ============================================================

set -e

SERVICES_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ "$EUID" -ne 0 ]; then
    echo "ОШИБКА: Запустите с sudo: sudo bash install.sh"
    exit 1
fi

# ============================================================
# 1. Установка основного сервиса ams-sensors.service
# ============================================================
SERVICE_NAME="ams-sensors.service"
SERVICE_SRC="$SERVICES_DIR/$SERVICE_NAME"
SERVICE_DST="/etc/systemd/system/$SERVICE_NAME"

if [ -f "$SERVICE_SRC" ]; then
    echo "============================================"
    echo "Установка сервиса $SERVICE_NAME..."
    echo "============================================"
    
    cp "$SERVICE_SRC" "$SERVICE_DST"
    echo "  - файл скопирован в $SERVICE_DST"
    
    systemctl daemon-reload
    echo "  - systemd перезагружен"
    
    systemctl enable "$SERVICE_NAME" 2>&1 || true
    echo "  - автозапуск включён"
    
    systemctl restart "$SERVICE_NAME" 2>&1 || true
    echo "  - сервис перезапущен"
    
    echo ""
    echo "Статус сервиса $SERVICE_NAME:"
    systemctl status "$SERVICE_NAME" --no-pager -l || true
    echo ""
else
    echo "ПРЕДУПРЕЖДЕНИЕ: Файл $SERVICE_SRC не найден, пропускаем"
fi

# ============================================================
# 2. Установка таймера автообновления
# ============================================================
UPDATE_SERVICE="auto-update.service"
UPDATE_TIMER="auto-update.timer"
UPDATE_SERVICE_SRC="$SERVICES_DIR/$UPDATE_SERVICE"
UPDATE_TIMER_SRC="$SERVICES_DIR/$UPDATE_TIMER"
UPDATE_SERVICE_DST="/etc/systemd/system/$UPDATE_SERVICE"
UPDATE_TIMER_DST="/etc/systemd/system/$UPDATE_TIMER"

if [ -f "$UPDATE_SERVICE_SRC" ] && [ -f "$UPDATE_TIMER_SRC" ]; then
    echo "============================================"
    echo "Установка таймера автообновления..."
    echo "============================================"
    
    cp "$UPDATE_SERVICE_SRC" "$UPDATE_SERVICE_DST"
    echo "  - $UPDATE_SERVICE скопирован"
    
    cp "$UPDATE_TIMER_SRC" "$UPDATE_TIMER_DST"
    echo "  - $UPDATE_TIMER скопирован"
    
    # Делаем скрипт обновления исполняемым
    if [ -f "$SERVICES_DIR/auto_update.sh" ]; then
        chmod +x "$SERVICES_DIR/auto_update.sh"
        echo "  - auto_update.sh сделан исполняемым"
    fi
    
    # Делаем скрипт синхронизации tz исполняемым
    if [ -f "$SERVICES_DIR/sync_timezone.sh" ]; then
        chmod +x "$SERVICES_DIR/sync_timezone.sh"
        echo "  - sync_timezone.sh сделан исполняемым"
    fi
    
    systemctl daemon-reload
    echo "  - systemd перезагружен"
    
    systemctl enable "$UPDATE_TIMER" 2>&1 || true
    echo "  - таймер включён"
    
    systemctl start "$UPDATE_TIMER" 2>&1 || true
    echo "  - таймер запущен"
    
    echo ""
    echo "Статус таймера $UPDATE_TIMER:"
    systemctl status "$UPDATE_TIMER" --no-pager -l || true
    echo ""
    
    echo "Ближайшие срабатывания таймера:"
    systemctl list-timers "$UPDATE_TIMER" --no-pager || true
    echo ""
else
    echo "ПРЕДУПРЕЖДЕНИЕ: Файлы таймера не найдены, пропускаем"
fi

# ============================================================
# 3. Итог
# ============================================================
echo "============================================"
echo "Установка завершена!"
echo "============================================"
echo ""
echo "Просмотр логов основного сервиса:"
echo "  journalctl -u $SERVICE_NAME -f"
echo ""
echo "Просмотр логов автообновления:"
echo "  journalctl -u $UPDATE_SERVICE"
echo ""
echo "Проверка таймеров:"
echo "  systemctl list-timers"