#!/bin/bash
set -e
source /opt/ros/jazzy/setup.bash
source "${RMF_WS:-$HOME/rmf_demos_ws}/install/setup.bash"
export ROS_DOMAIN_ID=211
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "$PROJECT_ROOT/robot_ws/src/inspection_robot/test/reliability_stack.py"
