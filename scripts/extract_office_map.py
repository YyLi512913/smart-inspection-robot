# 从 rmf office.building.yaml 提取墙体线段与 png 尺寸，生成前端大屏 SVG 用的 office_map.json
# 用法: wsl -d Ubuntu-24.04 -- /bin/bash -c "python3 /mnt/f/robot-inspection/scripts/extract_office_map.py"
import json
import re
import struct

YAML = "/mnt/f/robot-inspection/scripts/office.building.yaml"  # 先拷到 Windows 侧再解析
OUT = "/mnt/f/robot-inspection/frontend/public/office_map.json"
PNG = "/mnt/f/robot-inspection/scripts/office.png"

# png 尺寸（IHDR：8字节签名后，宽4字节+高4字节 大端）
with open(PNG, "rb") as f:
    head = f.read(24)
    W, H = struct.unpack(">II", head[16:24])

text = open(YAML, encoding="utf-8").read()

# 只取 levels.L1 段（nav_graphs 之前），避免混入其它顶点
lvl = text.split("\nlevels:")[1].split("\nnav_graphs:")[0]

# vertices 段：条目格式 - [x, y, z, ""]
vsec = lvl.split("\n    vertices:\n")[1].split("\n    walls:")[0]
verts = [(float(m.group(1)), float(m.group(2)))
         for m in re.finditer(r"\[\s*([\d.eE+-]+)\s*,\s*([\d.eE+-]+)\s*,", vsec)]

# walls 段：条目格式 - [v0, v1, {...}]
wsec = lvl.split("\n    walls:\n")[1]
walls_idx = re.findall(r"\[\s*(\d+)\s*,\s*(\d+)\s*,", wsec)
walls = []
for a, b in walls_idx:
    a, b = int(a), int(b)
    if a < len(verts) and b < len(verts):
        walls.append([round(verts[a][0], 1), round(verts[a][1], 1),
                      round(verts[b][0], 1), round(verts[b][1], 1)])

# doors 段：同为 [v0, v1, {...}]
dsec = lvl.split("\n    doors:\n")[1].split("\n    drawing:")[0]
doors_idx = re.findall(r"\[\s*(\d+)\s*,\s*(\d+)\s*,", dsec)
doors = []
for a, b in doors_idx:
    a, b = int(a), int(b)
    if a < len(verts) and b < len(verts):
        doors.append([round(verts[a][0], 1), round(verts[a][1], 1),
                      round(verts[b][0], 1), round(verts[b][1], 1)])

data = {"png_width": W, "png_height": H,
        "meters_per_pixel": 0.01,  # rmf 标准：x_m=px*0.01, y_m=(py-H)*0.01
        "walls": walls, "doors": doors}
import os
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(data, f)
print(f"verts={len(verts)} walls={len(walls)} doors={len(doors)} png={W}x{H} -> {OUT}")
