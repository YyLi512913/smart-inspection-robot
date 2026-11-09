#!/bin/bash
# 查 rmf_sim.log 中与渲染/传感器/model 加载相关的行
grep -inE 'sensor|render|ogre|camera|EGL|GPU|TinyRobot|model.*load|failed|error' ~/rmf_sim.log | grep -v 'level_name' | head -30
