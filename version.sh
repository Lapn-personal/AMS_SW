#!/bin/bash
# ============================================================
# version.sh — Управление версией проекта AMS_SW
# Использование:
#   bash version.sh          — показать текущую версию
#   bash version.sh major    — увеличить MAJOR (1.0.0 → 2.0.0)
#   bash version.sh minor    — увеличить MINOR (1.0.0 → 1.1.0)
#   bash version.sh patch    — увеличить PATCH (1.0.0 → 1.0.1)
#   bash version.sh set X.Y.Z — установить конкретную версию
# ============================================================

VERSION_FILE="$(cd "$(dirname "$0")" && pwd)/VERSION"

if [ ! -f "$VERSION_FILE" ]; then
    echo "ОШИБКА: Файл VERSION не найден"
    exit 1
fi

CURRENT_VERSION=$(cat "$VERSION_FILE" | tr -d ' \n\r\t')
echo "Текущая версия: $CURRENT_VERSION"

IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT_VERSION"

case "${1:-show}" in
    show)
        echo "$CURRENT_VERSION"
        ;;
    major)
        NEW_VERSION="$((MAJOR + 1)).0.0"
        echo "$NEW_VERSION" > "$VERSION_FILE"
        echo "Новая версия: $NEW_VERSION"
        ;;
    minor)
        NEW_VERSION="$MAJOR.$((MINOR + 1)).0"
        echo "$NEW_VERSION" > "$VERSION_FILE"
        echo "Новая версия: $NEW_VERSION"
        ;;
    patch)
        NEW_VERSION="$MAJOR.$MINOR.$((PATCH + 1))"
        echo "$NEW_VERSION" > "$VERSION_FILE"
        echo "Новая версия: $NEW_VERSION"
        ;;
    set)
        if [ -z "$2" ]; then
            echo "ОШИБКА: Укажите версию: bash version.sh set X.Y.Z"
            exit 1
        fi
        echo "$2" > "$VERSION_FILE"
        echo "Новая версия: $2"
        ;;
    *)
        echo "Использование:"
        echo "  bash version.sh          — показать версию"
        echo "  bash version.sh major    — увеличить MAJOR"
        echo "  bash version.sh minor    — увеличить MINOR"
        echo "  bash version.sh patch    — увеличить PATCH"
        echo "  bash version.sh set X.Y.Z — установить версию"
        exit 1
        ;;
esac
