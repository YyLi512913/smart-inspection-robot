#!/bin/bash
# 排查:gz sim 的模型搜索路径 + world 如何引用 TinyRobot + 相机话题
source /opt/ros/jazzy/setup.bash
GZPID=$(pgrep -f 'gz sim -s' | head -1)
echo "gz pid=$GZPID"
tr '\0' '\n' < /proc/$GZPID/environ | grep -i 'GZ_SIM_RESOURCE' || echo '(无 RESOURCE 变量!)'
echo '--- world 中 TinyRobot 引用 ---'
grep -n 'TinyRobot\|model//' /home/yanyang/rmf_demos_ws/install/rmf_demos_maps/share/rmf_demos_maps/maps/factory/factory.world | head
echo '--- 当前模型列表 ---'
gz topic -l | grep -iE 'camera|inspection' || echo '(无相机话题)'
echo '--- tinyRobot1 实体 ---'
gz model -m tinyRobot1 -i 0 2>&1 | head -5
