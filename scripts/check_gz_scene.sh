#!/bin/bash
source /opt/ros/jazzy/setup.bash
echo '--- render engine ---'
grep -i 'render engine\|ogre' ~/rmf_sim.log | head -5
echo '--- scene/info 中的 camera/sensor 实体 ---'
timeout 10 gz topic -e -t /world/sim_world/scene/info -n 1 2>/dev/null | grep -iE 'inspection_camera|sensor|camera' | head -10
echo '--- scene/info 行数(判断能否取到) ---'
timeout 10 gz topic -e -t /world/sim_world/scene/info -n 1 2>/dev/null | wc -l
