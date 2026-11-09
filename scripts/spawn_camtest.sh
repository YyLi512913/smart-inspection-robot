#!/bin/bash
# 手动 spawn override TinyRobot(含相机)为 tinyRobotCamTest,验证传感器能否创建
source /opt/ros/jazzy/setup.bash
REQ='filename: "/home/yanyang/rmf_demos_ws/src/rmf_assets_override/models/Open-RMF/TinyRobot/model.sdf" model_name: "tinyRobotCamTest"'
gz service -s /world/sim_world/create --reqtype gz.msgs.EntityFactory --reptype gz.msgs.Boolean --timeout 5000 --req "$REQ"
sleep 3
echo '--- camera topics ---'
gz topic -l | grep -i camera || echo '(无)'
echo '--- 出图测试 ---'
timeout 8 gz topic -e -t /world/sim_world/inspection_camera -n 1 2>&1 | head -8
