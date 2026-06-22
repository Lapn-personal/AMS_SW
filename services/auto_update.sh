#!/bin/bash
# ============================================================
# auto_update.sh — Автоматическое обновление ПО из Git
# 1. Синхронизирует часовой пояс с реальным местоположением
# 2. Забирает изменения из Git (fetch + hard reset)
# 3. Перезагружает устройство, если были обновления
# ============================================================

set -e

PROJECT_DIR="/home/ams-root/AMS_SW"
SERVICES_DIR="$PROJECT_DIR/services"

echo "[AUTO-UPDATE] ========================================"
echo "[AUTO-UPDATE] Запуск автообновления: $(date)"
echo "[AUTO-UPDATE] ========================================"

# 1. Синхронизация часового пояса (чтобы 3:00 было реально местным)
if [ -f "$SERVICES_DIR/sync_timezone.sh" ]; then
    echo "[AUTO-UPDATE] Синхронизация часового пояса..."
    bash "$SERVICES_DIR/sync_timezone.sh" 2>&1 || echo "[AUTO-UPDATE] Предупреждение: sync_timezone.sh завершился с ошибкой"
fi

# 2. Переходим в директорию проекта
if [ ! -d "$PROJECT_DIR" ]; then
    echo "[AUTO-UPDATE] ОШИБКА: директория проекта $PROJECT_DIR не найдена"
    exit 1
fi

cd "$PROJECT_DIR"

# 3. Проверяем доступность GitHub
echo "[AUTO-UPDATE] Проверка связи с GitHub..."
if ! timeout 10 git ls-remote --heads origin &>/dev/null; then
    echo "[AUTO-UPDATE] ОШИБКА: GitHub недоступен. Пропускаем обновление."
    exit 0
fi

# 4. Сохраняем текущий HEAD
OLD_HEAD=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
echo "[AUTO-UPDATE] Текущий HEAD: $OLD_HEAD"

# 5. Определяем текущую ветку
CURRENT_BRANCH=$(git branch --show-current 2>/dev/null || echo "main")
echo "[AUTO-UPDATE] Текущая ветка: $CURRENT_BRANCH"

# 6. Забираем изменения из удалённого репозитория
echo "[AUTO-UPDATE] Загрузка изменений из origin/$CURRENT_BRANCH..."
git fetch origin "$CURRENT_BRANCH" 2>&1 || {
    echo "[AUTO-UPDATE] ОШИБКА: не удалось выполнить git fetch"
    exit 0
}

# 7. Проверяем, есть ли новые коммиты
REMOTE_HEAD=$(git rev-parse "origin/$CURRENT_BRANCH" 2>/dev/null || echo "unknown")
echo "[AUTO-UPDATE] Удалённый HEAD: $REMOTE_HEAD"

if [ "$OLD_HEAD" = "$REMOTE_HEAD" ]; then
    echo "[AUTO-UPDATE] Обновлений нет. HEAD не изменился."
    exit 0
fi

if [ "$REMOTE_HEAD" = "unknown" ]; then
    echo "[AUTO-UPDATE] ОШИБКА: не удалось определить удалённый HEAD"
    exit 0
fi

# 8. Применяем изменения (hard reset — полная замена на origin)
echo "[AUTO-UPDATE] Применение изменений (git reset --hard)..."
git reset --hard "origin/$CURRENT_BRANCH" 2>&1

NEW_HEAD=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
echo "[AUTO-UPDATE] Новый HEAD: $NEW_HEAD"

# 9. Обновляем права на запуск скриптов
echo "[AUTO-UPDATE] Обновление прав на исполняемые файлы..."
find "$PROJECT_DIR" -name "*.sh" -type f -exec chmod +x {} \;

# 10. Перезагружаем systemd и сервис перед ребутом, чтобы обновлённый unit-файл применился
echo "[AUTO-UPDATE] Обновление systemd-конфигурации..."
if [ "$EUID" -eq 0 ]; then
    systemctl daemon-reload 2>&1 || true
fi

# 11. Логируем и перезагружаем устройство
echo "[AUTO-UPDATE] ========================================"
echo "[AUTO-UPDATE] Обновление применено: $OLD_HEAD -> $NEW_HEAD"
echo "[AUTO-UPDATE] Перезагрузка через 5 секунд..."
echo "[AUTO-UPDATE] ========================================"

# Даём время на запись логов перед перезагрузкой
sleep 5

if [ "$EUID" -eq 0 ]; then
    reboot
else
    echo "[AUTO-UPDATE] Нет прав root для перезагрузки."
    echo "[AUTO-UPDATE] Выполните вручную: sudo reboot"
fi