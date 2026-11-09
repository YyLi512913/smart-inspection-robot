#!/bin/bash
# 标记测试 v2:override(Open-RMF 命名空间)速度 0.5->0.45,重启仿真看日志
SDF=/home/yanyang/rmf_demos_ws/src/rmf_assets_override/models/Open-RMF/TinyRobot/model.sdf
grep -c 'inspection_camera' "$SDF" | xargs echo 'sensor 块数量:'
sed -i 's|<nominal_drive_speed>0.5</nominal_drive_speed>|<nominal_drive_speed>0.45</nominal_drive_speed>|' "$SDF"
bash /mnt/f/robot-inspection/scripts/stop_rmf_sim.sh > /dev/null 2>&1
bash ~/rmf_start.sh > /dev/null 2>&1
sleep 28
echo '--- drive speed 标记 ---'
grep 'nominal drive speed' ~/rmf_sim.log | head -2
# 还原标记
sed -i 's|<nominal_drive_speed>0.45</nominal_drive_speed>|<nominal_drive_speed>0.5</nominal_drive_speed>|' "$SDF"
