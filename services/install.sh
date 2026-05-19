#!/bin/bash
# ============================================================
# install.sh — Ручная установка systemd-сервиса AMS Sensors
# Запускать с sudo: sudo bash install.sh
# ============================================================

SERVICE_NAME="ams-sensors.service"
SERVICE_SRC="$(cd "$(dirname "$0")" && pwd)/$SERVICE_NAME"
SERVICE_DST="/etc/systemd/system/$SERVICE_NAME"

if [ "$EUID" -ne 0 ]; then
    echo "ОШИБКА: Запустите с sudo: sudo bash install.sh"
    exit 1
fi

if [ ! -f "$SERVICE_SRC" ]; then
    echo "ОШИБКА: Файл $SERVICE_SRC не найден"
    exit 1
fi

echo "Установка сервиса $SERVICE_NAME..."

# Копируем сервис
cp "$SERVICE_SRC" "$SERVICE_DST"
echo "  - файл скопирован в $SERVICE_DST"

# Перезагружаем systemd
systemctl daemon-reload
echo "  - systemd перезагружен"

# Включаем автозапуск
systemctl enable "$SERVICE_NAME"
echo "  - автозапуск включён"

# Запускаем
systemctl start "$SERVICE_NAME"
echo "  - сервис запущен"

# Проверяем статус
echo ""
echo "Статус сервиса:"
systemctl status "$SERVICE_NAME" --no-pager

echo ""
echo "Готово! Сервис $SERVICE_NAME установлен и запущен."
echo "Для просмотра логов: journalctl -u $SERVICE_NAME -f"
