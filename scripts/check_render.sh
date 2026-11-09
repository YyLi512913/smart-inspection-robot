#!/bin/bash
echo '--- 找 simulation.launch.xml ---'
find /opt/ros/jazzy/share ~/rmf_demos_ws -name 'simulation.launch.xml' 2>/dev/null | head -5
echo '--- rmf_sim.log 前 80 行找渲染 ---'
head -80 ~/rmf_sim.log | grep -iE 'engine|ogre|render|display|EGL' || echo '(无)'
echo '--- factory.world 的 physics/gui ---'
grep -n 'render_engine\|<physics\|<gui' /home/yanyang/rmf_demos_ws/install/rmf_demos_maps/share/rmf_demos_maps/maps/factory/factory.world | head
echo '--- 已安装的渲染引擎库 ---'
ls /opt/ros/jazzy/lib/gz-rendering-*/ 2>/dev/null | head; ls /usr/lib/x86_64-linux-gnu/ | grep -i 'gz-rendering\|libOgre' | head
