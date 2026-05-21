#!/bin/bash
# ============================================================
# i2c_setup.sh — Настройка I2C на Raspberry Pi
# Включает I2C в /boot/config.txt, загружает модули,
# добавляет пользователя в группу i2c.
#
# Запуск: sudo bash i2c_setup.sh
# ============================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Проверка прав root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}ОШИБКА: Запустите с sudo: sudo bash $0${NC}"
    exit 1
fi

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Настройка I2C для AMS Sensors${NC}"
echo -e "${GREEN}========================================${NC}"

# ========== 0. Установка i2c-tools ==========
echo ""
echo -e "${YELLOW}[0/5] Установка i2c-tools...${NC}"

if command -v i2cdetect &> /dev/null; then
    echo -e "  ${GREEN}✓ i2c-tools уже установлены${NC}"
else
    apt-get install -y -qq i2c-tools 2>&1 || echo -e "  ${YELLOW}⚠ Не удалось установить i2c-tools${NC}"
    if command -v i2cdetect &> /dev/null; then
        echo -e "  ${GREEN}✓ i2c-tools установлены${NC}"
    fi
fi

# ========== 1. Включение I2C в /boot/config.txt ==========
CONFIG_FILE="/boot/config.txt"
I2C_DT_PARAM="dtparam=i2c_arm=on"
I2C_BAUD_PARAM="dtparam=i2c_arm_baudrate=10000"

echo ""
echo -e "${YELLOW}[1/5] Проверка I2C в $CONFIG_FILE...${NC}"

if grep -q "^dtparam=i2c_arm=on" "$CONFIG_FILE" 2>/dev/null; then
    echo -e "  ${GREEN}✓ I2C уже включён в config.txt${NC}"
else
    echo "$I2C_DT_PARAM" >> "$CONFIG_FILE"
    echo -e "  ${GREEN}✓ I2C добавлен в config.txt (потребуется перезагрузка)${NC}"
fi

# Установка скорости I2C 10000 (10 кГц) для стабильной работы с длинными проводами
if grep -q "^dtparam=i2c_arm_baudrate" "$CONFIG_FILE" 2>/dev/null; then
    # Заменяем существующую строку
    sed -i "s/^dtparam=i2c_arm_baudrate=.*/$I2C_BAUD_PARAM/" "$CONFIG_FILE"
    echo -e "  ${GREEN}✓ Скорость I2C обновлена до 10000${NC}"
else
    echo "$I2C_BAUD_PARAM" >> "$CONFIG_FILE"
    echo -e "  ${GREEN}✓ Скорость I2C установлена на 10000 (10 кГц)${NC}"
fi


# ========== 2. Добавление модулей i2c в /etc/modules ==========
MODULES_FILE="/etc/modules"
I2C_MODULES=("i2c-dev" "i2c-bcm2835")

echo ""
echo -e "${YELLOW}[2/5] Проверка модулей I2C в $MODULES_FILE...${NC}"

for module in "${I2C_MODULES[@]}"; do
    if grep -q "^$module" "$MODULES_FILE" 2>/dev/null; then
        echo -e "  ${GREEN}✓ Модуль $module уже есть в /etc/modules${NC}"
    else
        echo "$module" >> "$MODULES_FILE"
        echo -e "  ${GREEN}✓ Модуль $module добавлен в /etc/modules${NC}"
    fi
done

# Загружаем модули сейчас (без перезагрузки)
echo ""
echo -e "${YELLOW}  Загрузка модулей сейчас...${NC}"
modprobe i2c-dev 2>/dev/null || true
modprobe i2c-bcm2835 2>/dev/null || true
echo -e "  ${GREEN}✓ Модули загружены${NC}"

# ========== 3. Добавление пользователя в группу i2c ==========
TARGET_USER="${SUDO_USER:-ams-root}"

echo ""
echo -e "${YELLOW}[3/5] Добавление пользователя $TARGET_USER в группу i2c...${NC}"

if getent group i2c > /dev/null 2>&1; then
    if id -nG "$TARGET_USER" | grep -qw "i2c"; then
        echo -e "  ${GREEN}✓ Пользователь $TARGET_USER уже в группе i2c${NC}"
    else
        usermod -aG i2c "$TARGET_USER"
        echo -e "  ${GREEN}✓ Пользователь $TARGET_USER добавлен в группу i2c${NC}"
    fi
else
    echo -e "  ${YELLOW}⚠ Группа i2c не существует. Создаю...${NC}"
    groupadd --system i2c
    usermod -aG i2c "$TARGET_USER"
    echo -e "  ${GREEN}✓ Группа i2c создана, пользователь $TARGET_USER добавлен${NC}"
fi

# ========== 4. Создание udev-правила для доступа к I2C ==========
UDEV_RULE_FILE="/etc/udev/rules.d/99-i2c.rules"

echo ""
echo -e "${YELLOW}[4/5] Настройка udev-правила для I2C...${NC}"

if [ -f "$UDEV_RULE_FILE" ]; then
    echo -e "  ${GREEN}✓ udev-правило уже существует${NC}"
else
    cat > "$UDEV_RULE_FILE" << 'EOF'
# Разрешить чтение/запись для группы i2c на всех устройствах I2C
SUBSYSTEM=="i2c-dev", GROUP="i2c", MODE="0660"
EOF
    echo -e "  ${GREEN}✓ udev-правило создано: $UDEV_RULE_FILE${NC}"
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger 2>/dev/null || true
    echo -e "  ${GREEN}✓ Правила udev перезагружены${NC}"
fi

# ========== ИТОГ ==========
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Настройка I2C завершена!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "Проверка доступных I2C-устройств:"
if command -v i2cdetect &> /dev/null; then
    i2cdetect -y 1 2>/dev/null || echo "  (устройства не найдены или шина недоступна)"
else
    echo "  (утилита i2cdetect не установлена)"
    echo "  Установите: sudo apt install i2c-tools"
fi
echo ""
echo -e "${YELLOW}⚠ ВАЖНО: Если I2C был только что включён в config.txt,${NC}"
echo -e "${YELLOW}  перезагрузите Raspberry Pi для применения изменений.${NC}"
echo ""
echo "После перезагрузки проверьте:"
echo "  ls /dev/i2c-*"
echo "  i2cdetect -y 1"
