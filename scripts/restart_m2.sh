#!/bin/bash
# 重启 m2.launch.py:先杀父 launch,再逐个杀子节点(防孤儿残留)
pkill -9 -f '[m]2.launch.py'
sleep 1
pkill -9 -f 'inspection_robot/lib/[t]ask_runner'
pkill -9 -f 'inspection_robot/lib/[s]napshot_node'
pkill -9 -f 'inspection_robot/lib/[b]attery' 2>/dev/null
sleep 1
echo '--- 残留检查 ---'
pgrep -af 'task_runner|snapshot_node|m2.launch' | grep -v pgrep || echo '(已清空)'
