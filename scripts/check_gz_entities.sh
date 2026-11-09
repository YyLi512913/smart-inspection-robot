#!/bin/bash
source /opt/ros/jazzy/setup.bash
echo '--- scene/info service (SceneFilter) ---'
timeout 12 gz service -s /world/sim_world/scene/info --reqtype gz.msgs.SceneFilter --reptype gz.msgs.Scene --timeout 8000 --req-data 'include_static: true' > /tmp/scene_dump.txt 2>&1
echo "dump lines: $(wc -l < /tmp/scene_dump.txt)"
grep -in 'camera\|sensor' /tmp/scene_dump.txt | head
echo '--- tinyRobot1 相关 ---'
grep -in 'tinyrobot' /tmp/scene_dump.txt | head
