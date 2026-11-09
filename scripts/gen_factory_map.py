# 工厂地图生成器：单一坐标源 → 三端产物
#   1) factory.building.yaml  (rmf Building Editor 格式, 像素坐标 100px=1m)
#   2) frontend/public/factory_map.json  (前端大屏 SVG, 墙体 y 已翻转成显示坐标)
#   3) factory.png  (2600x1600 纯色底图, 仅供 Building Editor 显示)
# 布局(米, nav graph 坐标系 y 向上为负):
#   外框 26x16m; 走廊 y∈[-6,0] 全宽; 车间 x∈[0,12] / 配电房 x∈[12,18] / 服务器机房 x∈[18,26], y∈[-16,-6]
#   三门在 y=-6 墙: 缺口 x=[5,6.5]/[14,15.5]/[21,22.5]
#   充电桩 tinyRobot1_charger (2,-3); 3 条走廊 lane 端点 + 3 个房间 place
# 关键机制(已验证):
#   - nav graph 只由 lanes 生成; door 与 lane 段几何相交则给该 lane 打 door_name 标签
#   - scale = mean(measurements.distance / 像素长度); 无 measurements 则 1.0(像素!) → 必须加
#   - nav 米坐标 = (x_px*0.01, -y_px*0.01); 前端 toPx=(x*100, H+y*100) → 墙体存 (X, H-Y) 即对齐
import json
import os
import struct
import zlib

SCALE = 0.01          # m/px, 强制 100px=1m
W_PX, H_PX = 2600, 1600

OUT_YAML = "/mnt/f/robot-inspection/scripts/factory.building.yaml"
OUT_JSON = "/mnt/f/robot-inspection/frontend/public/factory_map.json"
OUT_PNG = "/mnt/f/robot-inspection/scripts/factory.png"

# ---- 顶点表 (像素, png 空间: x 右, y 下) ----
V = [
    (0, 0), (2600, 0), (2600, 1600), (0, 1600),          # 0-3 外框角
    (0, 600), (500, 600), (650, 600), (1200, 600),        # 4-7 隔墙 y=600
    (1400, 600), (1550, 600), (1800, 600),                # 8-10
    (2100, 600), (2250, 600), (2600, 600),                # 11-13
    (1200, 1600), (1800, 1600),                           # 14-15 房间分隔墙底端
    (1000, 0),                                            # 16 measurement 端点
    (200, 300), (575, 300), (1475, 300), (2175, 300),     # 17-20 走廊 nav 顶点
    (575, 1100), (1475, 1100), (2175, 1100),              # 21-23 房间 place 顶点
]
CHARGER, COR_W, COR_M, COR_E = 17, 18, 19, 20
WORKSHOP, POWER, SERVER = 21, 22, 23

WALLS = [
    (0, 16), (16, 1),          # 上
    (1, 13), (13, 2),          # 右
    (2, 15), (15, 14), (14, 3),  # 下
    (3, 4), (4, 0),            # 左
    (4, 5), (6, 7), (7, 8), (9, 10), (10, 11), (12, 13),  # 隔墙 y=600(门洞留空)
    (7, 14), (10, 15),         # 车间/配电房、配电房/服务器机房分隔墙
]
DOORS = [(5, 6, "door_workshop"), (8, 9, "door_power"), (11, 12, "door_server")]
LANES = [
    (CHARGER, COR_W), (COR_W, COR_M), (COR_M, COR_E),
    (COR_W, WORKSHOP),   # 穿 door_workshop: (575,300)-(575,1100) 与门边 (500,600)-(650,600) 交于 t=0.375
    (COR_M, POWER),      # 交点 x=1475 ∈[1400,1550] ✓
    (COR_E, SERVER),     # 交点 x=2175 ∈[2100,2250] ✓
]
MEASUREMENTS = [(0, 16, 10.0)]  # 1000px = 10m → scale 恰为 0.01

FLOOR_POLY = [0, 1, 2, 3]

WALL_PROPS = "{alpha: [3, 1], texture_height: [3, 2.5], texture_name: [1, wall_white], texture_scale: [3, 1], texture_width: [3, 1]}"
LANE_PROPS = "{bidirectional: [4, true], demo_mock_floor_name: [1, \"\"], demo_mock_lift_name: [1, \"\"], graph_idx: [2, 0], mutex: [1, \"\"], orientation: [1, \"\"], speed_limit: [3, 0]}"


def door_props(name):
    return ("{motion_axis: [1, start], motion_degrees: [3, 90], motion_direction: [2, 1], "
            f"name: [1, {name}], plugin: [1, normal], right_left_ratio: [3, 1], type: [1, hinged]"
            "}")


def vertex_line(i):
    x, y = V[i]
    if i == CHARGER:
        props = ("{is_charger: [4, true], is_holding_point: [4, true], is_parking_spot: [4, true], "
                 "spawn_robot_name: [1, tinyRobot1], spawn_robot_type: [1, Open-RMF/TinyRobot]}")
        name = "tinyRobot1_charger"
    else:
        names = {COR_W: "corridor_west", COR_M: "corridor_mid", COR_E: "corridor_east",
                 WORKSHOP: "workshop", POWER: "power_room", SERVER: "server_room"}
        name = names.get(i, "")
        props = "{is_parking_spot: [4, false]}" if i in names else "{}"
    disp = name if name else '""'
    return f"      - [{x}, {y}, 0, {disp}, {props}]"


def build_yaml():
    L = []
    L.append("coordinate_system: reference_image")
    L.append("crowd_sim:")
    L.append("  agent_groups:")
    L.append("    - {agents_name: [tinyRobot1], agents_number: 1, group_id: 0, profile_selector: external_agent, state_selector: external_static, x: 0, y: 0}")
    L.append("  agent_profiles:")
    L.append("    - {ORCA_tau: 1, ORCA_tauObst: 0.4, class: 1, max_accel: 0, max_angle_vel: 0, max_neighbors: 10, max_speed: 0, name: external_agent, neighbor_dist: 5, obstacle_set: 1, pref_speed: 0, r: 0.25}")
    L.append("  enable: 0")
    L.append("  goal_sets: []")
    L.append("  model_types: []")
    L.append("  obstacle_set: {class: 1, file_name: L1_navmesh.nav, type: nav_mesh}")
    L.append("  states:")
    L.append("    - {final: 1, goal_set: -1, name: external_static, navmesh_file_name: \"\"}")
    L.append("  transitions: []")
    L.append("  update_time_step: 0.1")
    L.append("graphs: {}")
    L.append("levels:")
    L.append("  L1:")
    L.append("    doors:")
    for a, b, name in DOORS:
        L.append(f"      - [{a}, {b}, {door_props(name)}]")
    L.append("    drawing:")
    L.append("      filename: factory.png")
    L.append("    elevation: 0")
    L.append("    features: []")
    L.append("    floors:")
    L.append("      - parameters: {ceiling_scale: [3, 1], ceiling_texture: [1, concrete2], indoor: [2, 0], texture_name: [1, concrete2], texture_rotation: [3, 0], texture_scale: [3, 10]}")
    L.append(f"        vertices: {FLOOR_POLY}")
    L.append("    holes: []")
    L.append("    lanes:")
    for a, b in LANES:
        L.append(f"      - [{a}, {b}, {LANE_PROPS}]")
    L.append("    measurements:")
    for a, b, d in MEASUREMENTS:
        L.append(f"      - [{a}, {b}, {{distance: [3, {d}]}}]")
    L.append("    models: []")
    L.append("    vertices:")
    for i in range(len(V)):
        L.append(vertex_line(i))
    L.append("    walls:")
    for a, b in WALLS:
        L.append(f"      - [{a}, {b}, {WALL_PROPS}]")
    L.append("lifts: {}")
    L.append("name: building")
    return "\n".join(L) + "\n"


def build_json():
    # 前端显示坐标: (X, H-Y) 预翻转, 与 toPx(米) 同层
    walls = [[V[a][0], H_PX - V[a][1], V[b][0], H_PX - V[b][1]] for a, b in WALLS]
    doors = [[V[a][0], H_PX - V[a][1], V[b][0], H_PX - V[b][1]] for a, b, _ in DOORS]
    return {"png_width": W_PX, "png_height": H_PX, "meters_per_pixel": SCALE,
            "walls": walls, "doors": doors}


def write_png(path, w, h, rgb=(180, 180, 180)):
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


if __name__ == "__main__":
    with open(OUT_YAML, "w", encoding="utf-8", newline="\n") as f:
        f.write(build_yaml())
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(build_json(), f)
    write_png(OUT_PNG, W_PX, H_PX)
    print(f"yaml -> {OUT_YAML}; json -> {OUT_JSON}; png -> {OUT_PNG}")
