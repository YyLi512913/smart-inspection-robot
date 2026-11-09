#!/usr/bin/env python3
"""给 override TinyRobot 模型加相机传感器:插入到 base_footprint link 末尾(幂等)。"""
import re

PATH = '/home/yanyang/rmf_demos_ws/src/rmf_assets_override/models/TinyRobot/model.sdf'
SENSOR = '''      <!-- 巡检相机:车头 +x 方向,装于 base_footprint -->
      <sensor name='inspection_camera' type='camera'>
        <pose>0.25 0 0.28 0 0 0</pose>
        <camera>
          <horizontal_fov>1.05</horizontal_fov>
          <image>
            <width>640</width>
            <height>480</height>
            <format>R8G8B8</format>
          </image>
          <clip>
            <near>0.1</near>
            <far>50</far>
          </clip>
        </camera>
        <always_on>1</always_on>
        <update_rate>10</update_rate>
        <topic>/world/sim_world/inspection_camera</topic>
      </sensor>
'''

with open(PATH) as f:
    text = f.read()

if 'inspection_camera' in text:
    print('already patched')
else:
    # 找第一个 </link>(base_footprint 的结尾)前插入
    idx = text.index('</link>')
    text = text[:idx] + SENSOR + text[idx:]
    with open(PATH, 'w') as f:
        f.write(text)
    print('patched at link end')

# 校验 XML 可解析
import xml.etree.ElementTree as ET
ET.parse(PATH)
print('XML valid')
