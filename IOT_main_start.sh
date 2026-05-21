#!/bin/bash

SESSION="sensors_dashboard"
WINDOW="dashboard"

# Убиваем старую сессию
/usr/bin/tmux kill-session -t "$SESSION" 2>/dev/null

# Создаём сессию с одной панелью
/usr/bin/tmux new-session -d -s "$SESSION" -n "$WINDOW"

# Первое вертикальное разделение на левую и правую половины
/usr/bin/tmux split-window -h -t "$SESSION:$WINDOW"

# Левая половина: разделим на три вертикальные панели (0, 2, 4)
/usr/bin/tmux select-pane -t "$SESSION:$WINDOW.0"
/usr/bin/tmux split-window -v -p 66 -t "$SESSION:$WINDOW"   # верхняя средняя левая
/usr/bin/tmux split-window -v -p 50 -t "$SESSION:$WINDOW"   # нижняя левая

# Правая половина: разделим на три вертикальные панели (1, 3, 5)
/usr/bin/tmux select-pane -t "$SESSION:$WINDOW.1"
/usr/bin/tmux split-window -v -p 66 -t "$SESSION:$WINDOW"   # верхняя средняя правая
/usr/bin/tmux split-window -v -p 50 -t "$SESSION:$WINDOW"   # нижняя правая

# Теперь индексы панелей:
# 0 – левая верхняя (temp_worker)
# 1 – правая верхняя (distance_worker)
# 2 – левая средняя (mqtt_aggregator)
# 3 – правая средняя (wind_worker)
# 4 – левая нижняя (mpu6050)
# 5 – правая нижняя (htop)

# Функция запуска процесса в панели с авто-перезапуском
# Использует бесконечный цикл bash, чтобы перезапускать Python-скрипт при падении
# stdout/stderr перенаправляются в tmux (видно в панелях, но не пишется на SD)
run_in_pane() {
    local pane="$1"
    local cmd="$2"
    local name="$3"
    /usr/bin/tmux send-keys -t "$SESSION:$WINDOW.$pane" \
        "cd ~/AMS_SW/fw_eng && source ~/AMS_SW/fw_env/bin/activate && while true; do echo \"[ЗАПУСК $name]\"; $cmd 2>&1; echo \"[ПЕРЕЗАПУСК $name через 3 сек...]\"; sleep 3; done" C-m
}

# Запускаем все рабочие процессы с авто-перезапуском
run_in_pane 0 "python3 -u temp_worker.py" "TEMP"
run_in_pane 1 "python3 -u distance_worker.py" "DISTANCE"
run_in_pane 2 "python3 -u mqtt_aggregator.py" "AGGREGATOR"
run_in_pane 3 "python3 -u wind_worker_ads1115.py" "WIND"
run_in_pane 4 "python3 -u mpu6050_direct.py" "MPU6050"
run_in_pane 5 "htop"

# Если аргумент --daemon НЕ передан – подключаемся к консоли
if [ "$1" != "--daemon" ]; then
    /usr/bin/tmux attach-session -t "$SESSION"
fi
