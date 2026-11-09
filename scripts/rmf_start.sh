#!/bin/bash
# 启动 rmf_demos factory 仿真 + RMF 调度栈(后台,日志落文件)
source /opt/ros/jazzy/setup.bash
source /home/yanyang/rmf_demos_ws/install/setup.bash
# override 模型库前置:含 inspection_camera 传感器的 TinyRobot
export GZ_SIM_RESOURCE_PATH=/home/yanyang/rmf_demos_ws/src/rmf_assets_override/models:/opt/ros/jazzy/share/rmf_demos_assets/models
cd /home/yanyang

nohup ros2 launch rmf_demos_gz simulation.launch.xml map_name:=factory headless:=true > rmf_sim.log 2>&1 &
sleep 2
nohup ros2 launch rmf_demos factory.launch.xml use_sim_time:=true > rmf_factory.log 2>&1 &
sleep 1
echo "sim pid group:"
pgrep -f simulation.launch.xml
echo "factory pid group:"
pgrep -f "rmf_demos factory.launch.xml"
