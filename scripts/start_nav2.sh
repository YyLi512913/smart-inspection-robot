#!/bin/bash
# Primary single-robot simulation. Stop the legacy RMF stack before using domain 0.
set -e
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source /opt/ros/jazzy/setup.bash
for package in nav2_bringup ros_gz_sim ros_gz_bridge cv_bridge; do
  ros2 pkg prefix "$package" >/dev/null
done
python3 "$PROJECT_ROOT/scripts/gen_nav2_factory.py"
cd "$PROJECT_ROOT/robot_ws"
colcon build --symlink-install --packages-select inspection_robot
source install/setup.bash
export LIBGL_ALWAYS_SOFTWARE="${LIBGL_ALWAYS_SOFTWARE:-1}"
export GZ_PARTITION="${GZ_PARTITION:-inspection_factory}"
exec ros2 launch inspection_robot nav2_factory.launch.py "$@"
