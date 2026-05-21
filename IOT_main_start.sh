#!/bin/bash
# ============================================================
# IOT_main_start.sh — Запуск всех воркеров в tmux
# Каждый воркер в отдельном окне + окно dashboard
# ============================================================

SESSION="ams_sensors"
PROJECT_DIR="/home/ams-root/AMS_SW"
VENV_DIR="$PROJECT_DIR/fw_env"
FW_ENG_DIR="$PROJECT_DIR/fw_eng"

# Убиваем старую сессию, если есть
/usr/bin/tmux kill-session -t "$SESSION" 2>/dev/null

# Создаём новую сессию с первым окном (dashboard)
/usr/bin/tmux new-session -d -s "$SESSION" -n "dashboard"

# Функция запуска процесса в отдельном окне с авто-перезапуском
# Воркеры запускаются от ams-root (не root), чтобы файлы логов и настройки
# создавались с правильными правами
run_in_window() {
    local window_name="$1"
    local cmd="$2"
    local display_name="$3"
    /usr/bin/tmux new-window -t "$SESSION" -n "$window_name"
    /usr/bin/tmux send-keys -t "$SESSION:$window_name" \
        "cd $FW_ENG_DIR && source $VENV_DIR/bin/activate && while true; do echo \"[ЗАПУСК $display_name]\"; $cmd 2>&1; echo \"[ПЕРЕЗАПУСК $display_name через 3 сек...]\"; sleep 3; done" C-m
}

# Запускаем каждый воркер в отдельном окне
run_in_window "temp"      "python3 -u temp_worker.py"         "TEMP"
run_in_window "distance"  "python3 -u distance_worker.py"     "DISTANCE"
run_in_window "wind"      "python3 -u wind_worker_ads1115.py" "WIND"
run_in_window "mpu6050"   "python3 -u mpu6050_direct.py"      "MPU6050"
run_in_window "ina219"    "python3 -u ina219_reader.py"       "INA219"
run_in_window "aggregator" "python3 -u mqtt_aggregator.py"    "AGGREGATOR"
run_in_window "htop"      "htop"                              "HTOP"

# Настраиваем dashboard — окно с информацией о состоянии
/usr/bin/tmux send-keys -t "$SESSION:dashboard" \
    "cd $PROJECT_DIR && source $VENV_DIR/bin/activate && while true; do clear; echo '============================================'; echo '  AMS Sensors — Dashboard'; echo '  $(date)'; echo '============================================'; echo ''; echo '  Окна tmux:'; /usr/bin/tmux list-windows -t \"$SESSION\" 2>/dev/null | while IFS= read -r line; do echo \"    \$line\"; done; echo ''; echo '  Процессы воркеров:'; for p in temp_worker distance_worker wind_worker_ads1115 mpu6050_direct ina219_reader mqtt_aggregator; do pid=\$(pgrep -f \"python3.*\$p\" 2>/dev/null | head -1); if [ -n \"\$pid\" ]; then echo \"    ✓ \$p (PID \$pid)\"; else echo \"    ✗ \$p — не запущен\"; fi; done; echo ''; echo '  I2C устройства:'; ls -la /dev/i2c-* 2>/dev/null || echo '    (нет I2C)'; echo ''; echo '  Нажмите Ctrl+C для выхода из dashboard'; echo '  (воркеры продолжат работу в фоне)'; echo '============================================'; sleep 5; done" C-m

# Если аргумент --daemon НЕ передан — подключаемся к dashboard
if [ "$1" != "--daemon" ]; then
    /usr/bin/tmux attach-session -t "$SESSION:dashboard"
fi
