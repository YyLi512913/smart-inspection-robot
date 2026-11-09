#!/bin/bash
# 停掉 factory 仿真 + RMF 调度栈全部进程(不含 inspection_ws 的 m2 节点)
pkill -9 -f '[s]imulation.launch.xml'
pkill -9 -f 'rmf_demos [f]actory.launch.xml'
pkill -9 -f '[g]z sim'
pkill -9 -f '[p]arameter_bridge /clock'
pkill -9 -f '[r]mf_traffic_schedule'
pkill -9 -f '[r]mf_traffic_blockade'
pkill -9 -f '[r]mf_building_map_tools'
pkill -9 -f '[r]mf_visualization'
pkill -9 -f '[r]mf_fleet_adapter'
pkill -9 -f '[r]mf_task_ros2'
pkill -9 -f '[r]mf_demos_fleet_adapter'
pkill -9 -f '[r]viz2 -d'
sleep 2
echo '--- 残留 ---'
pgrep -af 'gz sim|rmf_|simulation.launch|factory.launch|rviz2' | grep -v pgrep || echo CLEARED
