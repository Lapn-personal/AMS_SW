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
# 0 – левая верхняя
# 1 – правая верхняя
# 2 – левая средняя
# 3 – правая средняя
# 4 – левая нижняя
# 5 – правая нижняя

# Запускаем все рабочие процессы
/usr/bin/tmux send-keys -t "$SESSION:$WINDOW.0" "cd ~/fw_eng && source ~/fw_env/bin/activate && python3 -u temp_worker.py" C-m
/usr/bin/tmux send-keys -t "$SESSION:$WINDOW.1" "cd ~/fw_eng && source ~/fw_env/bin/activate && python3 -u distance_worker.py" C-m
/usr/bin/tmux send-keys -t "$SESSION:$WINDOW.2" "cd ~/fw_eng && source ~/fw_env/bin/activate && python3 -u mqtt_aggregator.py" C-m
/usr/bin/tmux send-keys -t "$SESSION:$WINDOW.3" "cd ~/fw_eng && source ~/fw_env/bin/activate && python3 -u wind_worker_ads1115.py" C-m
/usr/bin/tmux send-keys -t "$SESSION:$WINDOW.4" "cd ~/fw_eng && source ~/fw_env/bin/activate && python3 -u mpu6050_direct.py" C-m
/usr/bin/tmux send-keys -t "$SESSION:$WINDOW.5" "htop" C-m

# Если аргумент --daemon НЕ передан – подключаемся к консоли
if [ "$1" != "--daemon" ]; then
    /usr/bin/tmux attach-session -t "$SESSION"
fi