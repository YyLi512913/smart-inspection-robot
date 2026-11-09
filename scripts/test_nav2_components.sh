#!/bin/bash
set -e
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=214
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$PROJECT_ROOT/robot_ws/install/setup.bash"
python3 -m unittest discover -s "$PROJECT_ROOT/robot_ws/src/inspection_robot/test" -p 'test_nav2_components.py' -v
