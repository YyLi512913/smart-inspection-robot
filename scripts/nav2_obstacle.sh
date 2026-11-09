#!/bin/bash
# Dynamic obstacle at the server-room door. Uses the current GZ_PARTITION.
set -e
source /opt/ros/jazzy/setup.bash
export GZ_PARTITION="${GZ_PARTITION:-inspection_factory}"
if [ "${1:-spawn}" = "delete" ]; then
  gz service -s /world/inspection_factory/remove --reqtype gz.msgs.Entity --reptype gz.msgs.Boolean --timeout 5000 --req 'name: "inspection_door_obstacle" type: MODEL'
else
  gz service -s /world/inspection_factory/create --reqtype gz.msgs.EntityFactory --reptype gz.msgs.Boolean --timeout 5000 --req 'sdf: "<sdf version=\"1.9\"><model name=\"inspection_door_obstacle\"><static>true</static><pose>21.75 -6 .6 0 0 0</pose><link name=\"box\"><collision name=\"collision\"><geometry><box><size>1.8 .6 1.2</size></box></geometry></collision><visual name=\"visual\"><geometry><box><size>1.8 .6 1.2</size></box></geometry><material><ambient>.8 .25 .12 1</ambient><diffuse>.8 .25 .12 1</diffuse></material></visual></link></model></sdf>"'
fi
