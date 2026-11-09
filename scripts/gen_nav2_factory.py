#!/usr/bin/env python3
"""Generate Gazebo collision geometry and Nav2 occupancy from the same factory walls."""
import importlib.util
import math
from equipment_models import equipment_models
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "robot_ws/src/inspection_robot"
spec = importlib.util.spec_from_file_location("factory", ROOT / "scripts/gen_factory_map.py")
factory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(factory)


def box(name, x, y, sx, sy, height=2.5, color=".65 .70 .61 1", z=None):
    z = height / 2 if z is None else z
    return f"""<model name="{name}"><static>true</static><pose>{x} {y} {z} 0 0 0</pose>
<link name="body"><collision name="collision"><geometry><box><size>{sx} {sy} {height}</size></box></geometry></collision>
<visual name="visual"><geometry><box><size>{sx} {sy} {height}</size></box></geometry>
<material><ambient>{color}</ambient><diffuse>{color}</diffuse></material></visual></link></model>"""


def build():
    for directory in ("worlds", "maps", "urdf"):
        (OUT / directory).mkdir(parents=True, exist_ok=True)
    blocks = []
    obstacles = []
    for i, (a, b) in enumerate(factory.WALLS):
        x1, y1 = factory.V[a][0]*.01, -factory.V[a][1]*.01
        x2, y2 = factory.V[b][0]*.01, -factory.V[b][1]*.01
        sx, sy = max(abs(x2-x1), .16), max(abs(y2-y1), .16)
        x, y = (x1+x2)/2, (y1+y2)/2
        blocks.append(box(f"wall_{i}", x, y, sx, sy))
        obstacles.append((x, y, sx, sy))
    # Equipment stays outside named inspection poses and doorway approaches.
    equipment = [
        ("power_cabinet_a",13,-14.5,.7,1,1.8,".73 .65 .35 1"),
        ("power_cabinet_b",16.5,-14.5,.7,1,1.8,".73 .65 .35 1"),
        ("server_rack_a",19.5,-14,1,1,2,".18 .24 .29 1"),
        ("server_rack_b",22,-14,1,1,2,".18 .24 .29 1"),
        ("server_rack_c",24.5,-14,1,1,2,".18 .24 .29 1"),
    ]
    for name,x,y,sx,sy,h,color in equipment:
        blocks.append(box(name,x,y,sx,sy,h,color))
        obstacles.append((x,y,sx,sy))
        if "rack" in name:
            # Visible equipment indicators provide actual scene details to inspect.
            for j in range(5):
                blocks.append(box(f"{name}_indicator_{j}",x,y+.51,.6,.015,.04,
                                  ".15 .8 .35 1", .4+j*.28))
    devices,footprints=equipment_models()
    blocks.extend(devices)
    obstacles.extend(footprints)
    blocks.append(box("charging_pad",2,-3,.8,.8,.015,".25 .55 .36 1"))
    robot = (OUT / "worlds/inspection_robot.sdf").read_text()
    world = f"""<?xml version="1.0"?>
<sdf version="1.9"><world name="inspection_factory">
<physics name="physics" type="ignored"><max_step_size>0.004</max_step_size><real_time_factor>1</real_time_factor></physics>
<plugin filename="gz-sim-physics-system" name="gz::sim::systems::Physics"/>
<plugin filename="gz-sim-user-commands-system" name="gz::sim::systems::UserCommands"/>
<plugin filename="gz-sim-scene-broadcaster-system" name="gz::sim::systems::SceneBroadcaster"/>
<plugin filename="gz-sim-sensors-system" name="gz::sim::systems::Sensors"><render_engine>ogre2</render_engine></plugin>
<scene><ambient>.6 .6 .6 1</ambient><background>.82 .87 .9 1</background><shadows>false</shadows></scene>
<light name="sun" type="directional"><pose>0 0 10 0 0 0</pose><diffuse>.9 .9 .9 1</diffuse><specular>.1 .1 .1 1</specular><direction>-.3 -.5 -1</direction></light>
<model name="floor"><static>true</static><link name="floor"><collision name="collision"><geometry><plane><normal>0 0 1</normal><size>60 60</size></plane></geometry></collision><visual name="visual"><geometry><plane><normal>0 0 1</normal><size>60 60</size></plane></geometry><material><ambient>.80 .82 .77 1</ambient><diffuse>.80 .82 .77 1</diffuse></material></visual></link></model>
{''.join(blocks)}
{robot}
</world></sdf>"""
    (OUT/"worlds/factory.sdf").write_text(world)
    # PGM row 0 is north. World rectangle [-1,27] x [-17,1], resolution 5 cm.
    resolution=.05
    width,height=560,360
    pixels=bytearray([254])*(width*height)
    for row in range(height):
        y=1-(row+.5)*resolution
        for col in range(width):
            x=-1+(col+.5)*resolution
            if any(abs(x-cx)<=sx/2 and abs(y-cy)<=sy/2 for cx,cy,sx,sy in obstacles):
                pixels[row*width+col]=0
    (OUT/"maps/factory.pgm").write_bytes(f"P5\n{width} {height}\n255\n".encode()+pixels)
    (OUT/"maps/factory.yaml").write_text(
        "image: factory.pgm\nmode: trinary\nresolution: 0.05\norigin: [-1.0, -17.0, 0.0]\nnegate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.25\n")
    print(f"Generated world and occupancy: {len(factory.WALLS)} walls, {len(equipment)} equipment items")


if __name__ == "__main__":
    build()
