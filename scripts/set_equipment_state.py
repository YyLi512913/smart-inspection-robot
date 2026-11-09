#!/usr/bin/env python3
"""Switch visible Gazebo equipment state only. Never supplies labels to the detector."""
import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def set_state(equipment_id,state):
    catalog=json.loads((ROOT/"robot_ws/src/inspection_robot/config/equipment_states.json").read_text())
    entry=catalog[equipment_id]
    gz=shutil.which("gz") or "/opt/ros/jazzy/opt/gz_tools_vendor/bin/gz"
    environment={**os.environ,"GZ_PARTITION":os.environ.get("GZ_PARTITION","inspection_factory")}
    # Hide first, show second. Both overlays have stable identities and no collision changes.
    for variant in ("normal","abnormal"):
        z=0 if variant==state else -20
        request=f'name: "{entry[variant+"_model"]}" position {{x:{entry["x"]} y:{entry["y"]} z:{z}}} orientation {{w:1}}'
        result=subprocess.run([gz,"service","-s","/world/inspection_factory/set_pose",
            "--reqtype","gz.msgs.Pose","--reptype","gz.msgs.Boolean","--timeout","5000","--req",request],
            env=environment,capture_output=True,text=True,check=True)
        if "data: true" not in result.stdout:raise RuntimeError(result.stdout+result.stderr)
    print(json.dumps({"equipment_id":equipment_id,"state":state,"partition":environment["GZ_PARTITION"]}))


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("equipment_id",choices=["ELEC-001","AIR-001","PIPE-001","all"])
    parser.add_argument("state",choices=["normal","abnormal"])
    args=parser.parse_args()
    for ident in (["ELEC-001","AIR-001","PIPE-001"] if args.equipment_id=="all" else [args.equipment_id]):
        set_state(ident,args.state)
