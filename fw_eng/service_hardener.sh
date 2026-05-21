#!/bin/bash
# ============================================================
# service_hardener.sh — Отключает все лишние systemd-службы
# для защиты SD-карты от износа.
#
# Читает список разрешённых служб из fw_settings/allowed_services.txt
# и отключает всё, чего в этом списке нет.
#
# Запуск: sudo bash service_hardener.sh
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ALLOWED_FILE="$PROJECT_DIR/fw_settings/allowed_services.txt"

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Проверка прав root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}ОШИБКА: Запустите с sudo: sudo bash $0${NC}"
    exit 1
fi

# Проверка наличия файла со списком
if [ ! -f "$ALLOWED_FILE" ]; then
    echo -e "${RED}ОШИБКА: Файл $ALLOWED_FILE не найден${NC}"
    exit 1
fi

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Service Hardener для AMS Sensors${NC}"
echo -e "${GREEN}========================================${NC}"
echo "Разрешённые службы из: $ALLOWED_FILE"
echo ""

# Читаем список разрешённых служб (игнорируем комментарии и пустые строки)
ALLOWED_SERVICES=()
while IFS= read -r line; do
    # Убираем комментарии
    line="${line%%#*}"
    # Убираем пробелы
    line="${line// /}"
    line="${line//	/}"
    if [ -n "$line" ]; then
        ALLOWED_SERVICES+=("$line")
    fi
done < "$ALLOWED_FILE"

echo -e "${YELLOW}Разрешённые службы (${#ALLOWED_SERVICES[@]} шт.):${NC}"
for svc in "${ALLOWED_SERVICES[@]}"; do
    echo "  ✓ $svc"
done
echo ""

# Функция проверки, есть ли служба в списке разрешённых
is_allowed() {
    local service="$1"
    for allowed in "${ALLOWED_SERVICES[@]}"; do
        if [ "$service" = "$allowed" ]; then
            return 0
        fi
    done
    return 1
}

# Получаем список всех включённых служб
echo -e "${YELLOW}Поиск включённых служб...${NC}"
ENABLED_SERVICES=$(systemctl list-unit-files --type=service --state=enabled --no-legend 2>/dev/null | awk '{print $1}' || true)

STOPPED_COUNT=0
DISABLED_COUNT=0
SKIPPED_COUNT=0

while IFS= read -r service; do
    [ -z "$service" ] && continue
    
    if is_allowed "$service"; then
        echo -e "  ${GREEN}✓ $service${NC} (оставлен)"
        SKIPPED_COUNT=$((SKIPPED_COUNT + 1))
    else
        echo -e "  ${RED}✗ $service${NC} — отключается..."
        
        # Останавливаем службу
        if systemctl is-active --quiet "$service" 2>/dev/null; then
            systemctl stop "$service" 2>/dev/null || true
            STOPPED_COUNT=$((STOPPED_COUNT + 1))
            echo "    → остановлен"
        fi
        
        # Отключаем службу
        if systemctl is-enabled --quiet "$service" 2>/dev/null; then
            systemctl disable "$service" 2>/dev/null || true
            DISABLED_COUNT=$((DISABLED_COUNT + 1))
            echo "    → отключён"
        fi
    fi
done <<< "$ENABLED_SERVICES"

# Также проверяем активные, но не включённые службы (например, запущенные вручную)
echo ""
echo -e "${YELLOW}Проверка активных служб (включая запущенные вручную)...${NC}"
ACTIVE_SERVICES=$(systemctl list-units --type=service --state=running --no-legend 2>/dev/null | awk '{print $1}' || true)

while IFS= read -r service; do
    [ -z "$service" ] && continue
    
    if ! is_allowed "$service"; then
        if systemctl is-active --quiet "$service" 2>/dev/null; then
            echo -e "  ${RED}✗ $service${NC} — активна, но не в списке. Останавливаю..."
            systemctl stop "$service" 2>/dev/null || true
            systemctl disable "$service" 2>/dev/null || true
            STOPPED_COUNT=$((STOPPED_COUNT + 1))
            DISABLED_COUNT=$((DISABLED_COUNT + 1))
        fi
    fi
done <<< "$ACTIVE_SERVICES"

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Готово!${NC}"
echo "  Оставлено:  $SKIPPED_COUNT"
echo "  Остановлено: $STOPPED_COUNT"
echo "  Отключено:   $DISABLED_COUNT"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "Чтобы применить новый список после изменений:"
echo "  sudo bash $SCRIPT_DIR/service_hardener.sh"
