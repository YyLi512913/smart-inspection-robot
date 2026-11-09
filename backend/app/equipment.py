"""Equipment registry and deterministic target expansion. Scene state is never a detection input."""
import json
from pathlib import Path
from . import db

BASE_PLACES = {
 "tinyRobot1_charger": (2.,-3.,"充电桩"),"corridor_west":(5.75,-3.,"西走廊"),
 "corridor_mid":(14.75,-3.,"中走廊"),"corridor_east":(21.75,-3.,"东走廊"),
 "workshop":(5.75,-11.,"生产车间"),"power_room":(14.75,-11.,"配电房"),"server_room":(21.75,-11.,"服务器机房")
}


def decode(value):
    return json.loads(value) if isinstance(value,str) else value


def seed_catalog():
    data=json.loads((Path(__file__).resolve().parents[2]/"shared/equipment_catalog.json").read_text(encoding="utf-8"))
    with db.transaction() as conn:
        for asset in data["equipment"]:
            conn.execute(db.text(
                "INSERT IGNORE INTO equipment(equipment_id,name,equipment_type,area) VALUES(:i,:n,:t,:a)"),
                {"i":asset["id"],"n":asset["name"],"t":asset["type"],"a":asset["area"]})
            for point in asset["observations"]:
                conn.execute(db.text(
                    "INSERT IGNORE INTO observation_point(observation_id,equipment_id,name,x,y,yaw) VALUES(:i,:e,:n,:x,:y,:w)"),
                    {"i":point["id"],"e":asset["id"],"n":point["name"],"x":point["x"],"y":point["y"],"w":point["yaw"]})
                for seq,check in enumerate(point["checks"],1):
                    conn.execute(db.text(
                        "INSERT IGNORE INTO observation_check(observation_id,seq,check_code,definition) VALUES(:i,:s,:c,:d)"),
                        {"i":point["id"],"s":seq,"c":check["code"],"d":json.dumps(check,ensure_ascii=False)})


def list_equipment(area=None, equipment_id=None):
    sql="SELECT * FROM equipment WHERE enabled=1"
    args={}
    if area:sql+=" AND area=:a";args["a"]=area
    if equipment_id:sql+=" AND equipment_id=:i";args["i"]=equipment_id
    assets=db.query(sql+" ORDER BY equipment_id",args)
    for asset in assets:
        points=db.query("SELECT * FROM observation_point WHERE equipment_id=:i AND enabled=1 ORDER BY observation_id",{"i":asset["equipment_id"]})
        for point in points:
            point["checks"]=[decode(r["definition"]) for r in db.query(
                "SELECT definition FROM observation_check WHERE observation_id=:i ORDER BY seq",{"i":point["observation_id"]})]
        asset["observations"]=points
    return assets


def point_from_observation(asset, point):
    snapshot={"equipment_id":asset["equipment_id"],"equipment_name":asset["name"],
              "equipment_type":asset["equipment_type"],"area":asset["area"],
              "observation_id":point["observation_id"],"observation_name":point["name"],
              "yaw":point["yaw"],"checks":point["checks"]}
    return {"place":point["observation_id"],"x":point["x"],"y":point["y"],
            "check_item":"；".join(c["name"] for c in point["checks"])[:128],
            "inspection":snapshot}


def expand_targets(targets):
    if not isinstance(targets,list) or not targets:
        raise ValueError("至少选择一个区域、设备或观察点")
    assets=list_equipment()
    observations={p["observation_id"]:(a,p) for a in assets for p in a["observations"]}
    out=[];seen=set()
    for target in targets:
        if not isinstance(target,dict):raise ValueError("巡检目标必须为对象")
        keys=[k for k in ("place","area","equipment_id","observation_id") if target.get(k)]
        if len(keys)!=1:raise ValueError("每个目标只允许选择区域、设备或观察点中的一种")
        key=keys[0];value=target[key];selected=[]
        if key in ("place","area") and value in BASE_PLACES:
            matches=[a for a in assets if a["area"]==value]
            if matches:
                selected=sorted([point_from_observation(a,p) for a in matches for p in a["observations"]],key=lambda p:(p["x"],p["y"]))
            else:
                x,y,_=BASE_PLACES[value]
                selected=[{"place":value,"x":x,"y":y,"check_item":target.get("check_item","")}]
        elif key=="equipment_id":
            matches=[a for a in assets if a["equipment_id"]==value]
            if not matches:raise ValueError(f"设备 {value} 不存在或已停用")
            selected=[point_from_observation(a,p) for a in matches for p in a["observations"]]
        elif key=="observation_id" or (key=="place" and value in observations):
            if value not in observations:raise ValueError(f"观察点 {value} 不存在或已停用")
            selected=[point_from_observation(*observations[value])]
        else:
            raise ValueError(f"未知巡检目标 {value}，请先查询设备台账")
        if not selected:raise ValueError(f"{value} 没有可执行观察点")
        for point in selected:
            if point["place"] not in seen:out.append(point);seen.add(point["place"])
    if len(out)>100:raise ValueError("单次巡检最多 100 个观察点")
    return out
