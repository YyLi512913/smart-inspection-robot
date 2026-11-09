#!/usr/bin/env bash
# 在 factory 仿真世界服务器机房门口 (21.75,-6) 生成/删除静态障碍箱（场景3 异常闭环测试用）
# 用法: bash spawn_obstacle.sh [spawn|delete]
OP="${1:-spawn}"
REQ='sdf: "<sdf version=\"1.7\"><model name=\"obstacle_box_serverdoor\"><static>true</static><pose>21.75 -6 0.5 0 0 0</pose><link name=\"obstacle_link\"><collision name=\"c\"><geometry><box><size>2 0.6 1</size></box></geometry></collision><visual name=\"v\"><geometry><box><size>2 0.6 1</size></box></geometry></visual></link></model></sdf>"'
if [ "$OP" = "delete" ]; then
  gz service -s /world/sim_world/remove --reqtype gz.msgs.Entity --reptype gz.msgs.Boolean --timeout 5000 \
    --req 'name: "obstacle_box_serverdoor" type: MODEL' && echo "deleted"
  gz service -s /world/sim_world/remove --reqtype gz.msgs.Entity --reptype gz.msgs.Boolean --timeout 5000 \
    --req 'name: "test_light" type: LIGHT'
else
  gz service -s /world/sim_world/create --reqtype gz.msgs.EntityFactory --reptype gz.msgs.Boolean --timeout 5000 \
    --req "$REQ" && echo "spawned"
fi
