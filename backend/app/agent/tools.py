"""Agent 工具集（设计文档 5.3）：10 个工具，全部直调后端内部函数。

负面清单（架构约束，永不提供）：直发 MQTT 报文、直控轮子速度、LLM 规划路径——
凡绕过状态机/续航校验/审计直达执行层的能力一律不给 Agent。

统一返回 {"ok": bool, "summary": str, "detail": any}：
summary 一句话结果（回给 LLM + 前端展示），detail 完整 JSON（前端可展开审计）。
"""
import json
import hashlib

import redis

from .. import config, db, energy_assessor, state_machine, equipment

_r = redis.Redis.from_url(config.REDIS_URL, decode_responses=True)

# factory 导航图 7 个命名点位（与 frontend/src/places.js、机器人侧 task_runner.PLACES 一致）
# key -> (x, y, 中文名)
PLACES = equipment.BASE_PLACES


# 危险写工具：框架层强制 ask_user 二次确认后才执行（设计文档 5.3 行为规则）
DANGEROUS = {"cancel_task", "decide_blocked", "decide_low_battery"}


def _ok(summary, detail=None):
    return {"ok": True, "summary": summary, "detail": detail}


def _err(summary):
    return {"ok": False, "summary": summary, "detail": None}


def _live(robot_id: str) -> dict | None:
    raw = _r.get(f"robot:{robot_id}:live")
    return json.loads(raw) if raw else None


def _resolve_points(points):
    try:
        return equipment.expand_targets(points), None
    except ValueError as exc:
        return None, str(exc)


# ---------------------------------------------------------------- 读操作 6

def list_places():
    assets=equipment.list_equipment()
    items=[{"place":k,"x":v[0],"y":v[1],"name_cn":v[2],
            "equipment_count":sum(a["area"]==k for a in assets),
            "expands_to_equipment":any(a["area"]==k for a in assets)} for k,v in PLACES.items()]
    return _ok(f"共 {len(items)} 个区域/位置；有设备的区域会自动展开为设备观察点",items)


def list_equipment(area=None, equipment_id=None):
    items=equipment.list_equipment(area=area,equipment_id=equipment_id)
    return _ok(f"共 {len(items)} 台设备，含观察点与固定检查项",items)


def query_robot_status(robot_id: str | None = None) -> dict:
    ids = [robot_id] if robot_id else sorted(set(config.ROBOT_IDS))
    out = []
    for rid in ids:
        live = _live(rid)
        if live is None:
            out.append({"robot_id": rid, "online": False})
        else:
            out.append({"robot_id": rid, "online": True, **live})
    online = [o for o in out if o["online"]]
    if not online:
        return _err("所有机器人都离线（无实时遥测）")
    summary = "；".join(
        f"{o['robot_id']} 电量 {o['battery']:.0f}% 位置({o['pos']['x']:.1f},{o['pos']['y']:.1f})"
        for o in online
    )
    return _ok(summary, out)


def query_task_status(task_no: str) -> dict:
    task = db.query_one("SELECT * FROM task WHERE task_no=:n", {"n": task_no})
    if task is None:
        return _err(f"任务 {task_no} 不存在")
    task["points"] = db.query(
        "SELECT seq, place, x, y, check_item, status FROM task_point "
        "WHERE task_id=:t ORDER BY seq", {"t": task["id"]})
    task["events"] = db.query(
        "SELECT event, point_seq, created_at FROM task_event "
        "WHERE task_id=:t ORDER BY id DESC LIMIT 5", {"t": task["id"]})
    for k in ("id", "created_at", "dispatched_at", "started_at", "finished_at"):
        task.pop(k, None)
    return _ok(f"{task_no} 状态 {task['status']}，机器人 {task['robot_id']}", task)


def list_tasks(status: str | None = None, robot_id: str | None = None,
               limit: int = 20) -> dict:
    sql = ("SELECT task_no, robot_id, source, status, priority, created_at "
           "FROM task WHERE 1=1")
    params: dict = {}
    if status:
        sql += " AND status=:s"
        params["s"] = status
    if robot_id:
        sql += " AND robot_id=:r"
        params["r"] = robot_id
    sql += " ORDER BY id DESC LIMIT :l"
    params["l"] = min(int(limit), 100)
    rows = db.query(sql, params)
    if not rows:
        return _ok("没有符合条件的任务", [])
    summary = "；".join(f"{r['task_no']}={r['status']}" for r in rows[:8])
    return _ok(f"共 {len(rows)} 条：{summary}", rows)


def query_results(task_no=None, place=None, equipment_id=None, limit=20):
    from ..results_api import list_results
    rows=list_results(task_no=task_no,place=place,equipment_id=equipment_id,limit=limit)
    abnormal=[row for row in rows if row["normal"]==0]
    pending=sum(row.get("review_required",False) for row in rows)
    return _ok(f"共 {len(rows)} 条结果，异常 {len(abnormal)} 条，需复核 {pending} 条",rows)


def check_task_feasibility(robot_id: str, points: list[dict]) -> dict:
    live = _live(robot_id)
    if live is None:
        return _err(f"{robot_id} 离线：无实时遥测，无法评估续航")
    pts, err = _resolve_points(points)
    if err:
        return _err(err)
    verdict = energy_assessor.assess(pts, live["pos"]["x"], live["pos"]["y"],
                                     live["battery"])
    label = {"feasible": "电量足够", "can_reach_dock": "跑不完但够回桩（任务等待电量恢复，主动回桩尚未接入）",
             "cannot_move": "电量过低连桩都到不了（需人工换电池）"}[verdict["verdict"]]
    return _ok(f"{verdict['verdict']}：{label}。里程 {verdict['route_m']}m 需电 "
               f"{verdict['need_pct']}%，当前 {verdict['battery']:.0f}%", verdict)


# ---------------------------------------------------------------- 写操作 4

def create_patrol_task(points: list[dict], priority: int = 5,
                       robot_id: str | None = None, request_key: str | None = None, actor: str = "agent") -> dict:
    robot_id = robot_id or config.ROBOT_IDS[0]
    if request_key and db.query_one("SELECT task_no FROM task_request WHERE request_key=:k", {"k":request_key}):
        task_no=state_machine.create_task(robot_id,"chat",int(priority),actor,[],{},request_key=request_key,request_identity=points)
        return _ok(f"任务 {task_no} 已创建，本次重复请求未再次执行",{"task_no":task_no})
    pts, err = _resolve_points(points)
    if err:
        return _err(err)
    live = _live(robot_id)
    if live is None:
        return _err(f"{robot_id} 离线：无法创建任务（续航评估前置条件缺失）")
    # 后端续航硬校验兜底护栏（设计文档 5.3）：Agent 跳过 check_task_feasibility 也会被拦
    verdict = energy_assessor.assess(pts, live["pos"]["x"], live["pos"]["y"],
                                     live["battery"])
    task_no = state_machine.create_task(robot_id, "chat", int(priority), actor,
                                        pts, verdict, request_key=request_key, request_identity=points)
    去向 = {"feasible": "已进入下发队列，等待机器人接单", "can_reach_dock": "转 DEFERRED，充到阈值自动下发",
            "cannot_move": "保持 CREATED，电量过低需人工换电池"}[verdict["verdict"]]
    return _ok(f"任务 {task_no} 创建成功：{去向}（电量 {verdict['battery']:.0f}%，"
               f"需 {verdict['need_pct']}%）", {"task_no": task_no, "energy": verdict})


def cancel_task(task_no: str, actor: str = "agent") -> dict:
    if not state_machine.cancel(task_no, operator=actor):
        return _err(f"任务 {task_no} 不存在或已终态，无法取消")
    return _ok(f"任务 {task_no} 取消请求已记录；机器人占用将在执行节点确认撤销后释放")


def decide_blocked(task_no: str, action: str, actor: str = "agent") -> dict:
    if action not in ("retry", "skip"):
        return _err("action 只能是 retry（重试）或 skip（跳过阻塞点）")
    target = state_machine.decision(task_no, action, operator="agent")
    if target is None:
        return _err(f"任务 {task_no} 不处于 BLOCKED，无法决策")
    return _ok(f"任务 {task_no} 决策 {'重试' if action == 'retry' else '跳过阻塞点'}，状态→{target}")


def decide_low_battery(task_no: str, action: str, actor: str = "agent") -> dict:
    """低电量请示决策：manual_swap=换电池后核实电量再放行；reschedule=取消改期。

    设计文档 5.5-C：放行靠遥测检测电量回升，不轻信口头声明。
    """
    task = db.query_one("SELECT * FROM task WHERE task_no=:n", {"n": task_no})
    if task is None:
        return _err(f"任务 {task_no} 不存在")
    if action == "reschedule":
        if not state_machine.cancel(task_no, operator="agent"):
            return _err(f"任务 {task_no} 当前 {task['status']}，无法取消")
        return _ok(f"任务 {task_no} 已取消（改期）")
    if action != "manual_swap":
        return _err("action 只能是 manual_swap（换电池放行）或 reschedule（取消改期）")
    if task["status"] not in ("CREATED", "DEFERRED"):
        return _err(f"任务 {task_no} 当前 {task['status']}，不是等待电量恢复的任务")
    live = _live(task["robot_id"])
    if live is None:
        return _err(f"{task['robot_id']} 离线，无法核实电量")
    pts = db.query("SELECT seq, place, x, y, check_item FROM task_point "
                   "WHERE task_id=:t AND status <> 'skipped' ORDER BY seq",
                   {"t": task["id"]})
    verdict = energy_assessor.assess(pts, live["pos"]["x"], live["pos"]["y"],
                                     live["battery"])
    if verdict["verdict"] != "feasible":
        return _ok(f"电量未恢复到可执行水平（当前 {live['battery']:.0f}%，"
                   f"评估 {verdict['verdict']}），任务保持等待", verdict)
    with db.transaction() as conn:
        t = state_machine._locked_task(conn, task_no)
        if t is None or t["status"] not in ("CREATED", "DEFERRED"):
            return _err(f"任务 {task_no} 状态已变化（{t['status'] if t else '不存在'}）")
        state_machine.audit(conn, t["id"], t["robot_id"], "decision_manual_swap",
                            detail={"battery": live["battery"], "energy": verdict,"operator":actor})
        state_machine.dispatch(conn, dict(t), "manual_swap_resume",
                               detail={"energy": verdict})
    return _ok(f"已核实电量回升（{live['battery']:.0f}%），任务 {task_no} 放行下发",
               {"battery": live["battery"], "energy": verdict})


# ---------------------------------------------------------------- 注册表

_HANDLERS = {
    "list_places": list_places,
    "list_equipment": list_equipment,
    "query_robot_status": query_robot_status,
    "query_task_status": query_task_status,
    "list_tasks": list_tasks,
    "query_results": query_results,
    "check_task_feasibility": check_task_feasibility,
    "create_patrol_task": create_patrol_task,
    "cancel_task": cancel_task,
    "decide_blocked": decide_blocked,
    "decide_low_battery": decide_low_battery,
}

_POINT_SCHEMA = {
    "type": "object",
    "properties": {
        "place": {"type": "string", "description": "地点名，必须来自 list_places 枚举"},
        "check_item": {"type":"string","description":"区域场景的补充检查说明"},
        "equipment_id": {"type":"string","description":"设备编号，来自 list_equipment，如 ELEC-001"},
        "observation_id": {"type":"string","description":"观察点编号，来自 list_equipment"},
        "area": {"type":"string","description":"区域名；与 place、equipment_id、observation_id 四选一"},
    },
    "minProperties": 1,
}

TOOL_SCHEMAS = [
    {"type":"function","function":{"name":"list_equipment","description":"查询设备台账、设备编号、观察点和检查规则。设备级巡检前先查询，不能猜测设备编号。","parameters":{"type":"object","properties":{"area":{"type":"string"},"equipment_id":{"type":"string"}}}}},
    {"type": "function", "function": {
        "name": "list_places",
        "description": "列出全部可巡检地点（英文名+中文名+坐标）。任何涉及地点的操作前先调用确认地点存在。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "query_robot_status",
        "description": "查询机器人实时状态（在线/电量/位置）。不传 robot_id 查全部。",
        "parameters": {"type": "object", "properties": {
            "robot_id": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "query_task_status",
        "description": "查询单个任务详情：状态、点位、最近事件。",
        "parameters": {"type": "object", "properties": {
            "task_no": {"type": "string", "description": "任务号，形如 T20261107-001"}},
            "required": ["task_no"]}}},
    {"type": "function", "function": {
        "name": "list_tasks",
        "description": "列出任务（可按状态/机器人过滤，最新在前）。用户用'刚才那个任务'等指代时靠它消解。",
        "parameters": {"type": "object", "properties": {
            "status": {"type": "string", "enum": ["CREATED", "DISPATCHED", "ACCEPTED",
                      "NAVIGATING", "INSPECTING", "REPORTING", "BLOCKED", "DEFERRED",
                      "DONE", "CANCELED", "TIMEOUT", "FAILED"]},
            "robot_id": {"type": "string"},
            "limit": {"type": "integer", "default": 20}}}}},
    {"type": "function", "function": {
        "name": "query_results",
        "description": "查询巡检结果记录（点位上报元数据 + VLM 检测结论 normal/reading/anomaly，可按任务/地点过滤）。",
        "parameters": {"type": "object", "properties": {
            "task_no": {"type": "string"},
            "place": {"type": "string"},
            "equipment_id": {"type": "string"},
            "limit": {"type": "integer", "default": 20}}}}},
    {"type": "function", "function": {
        "name": "check_task_feasibility",
        "description": "续航可行性预演（不创建任务）：返回 feasible/can_reach_dock/cannot_move 与里程耗电明细。创建任务前必调。",
        "parameters": {"type": "object", "properties": {
            "robot_id": {"type": "string"},
            "points": {"type": "array", "items": _POINT_SCHEMA}},
            "required": ["robot_id", "points"]}}},
    {"type": "function", "function": {
        "name": "create_patrol_task",
        "description": "创建巡检任务（source=chat）。后端强制续航硬校验兜底。调用前必须先 check_task_feasibility 并与用户确认方案。",
        "parameters": {"type": "object", "properties": {
            "points": {"type": "array", "items": _POINT_SCHEMA, "description": "按执行顺序排列"},
            "priority": {"type": "integer", "minimum": 1, "maximum": 9, "default": 5},
            "robot_id": {"type": "string"}},
            "required": ["points"]}}},
    {"type": "function", "function": {
        "name": "cancel_task",
        "description": "取消任务（危险写操作，系统会弹出让用户二次确认）。",
        "parameters": {"type": "object", "properties": {
            "task_no": {"type": "string"}}, "required": ["task_no"]}}},
    {"type": "function", "function": {
        "name": "decide_blocked",
        "description": "对 BLOCKED 任务做决策（危险写操作，需用户二次确认）：retry=重试阻塞点，skip=跳过续跑。",
        "parameters": {"type": "object", "properties": {
            "task_no": {"type": "string"},
            "action": {"type": "string", "enum": ["retry", "skip"]}},
            "required": ["task_no", "action"]}}},
    {"type": "function", "function": {
        "name": "decide_low_battery",
        "description": "对低电量等待中（CREATED/DEFERRED）任务做决策（危险写操作，需用户二次确认）：manual_swap=用户换完电池后核实电量并放行；reschedule=取消改期。",
        "parameters": {"type": "object", "properties": {
            "task_no": {"type": "string"},
            "action": {"type": "string", "enum": ["manual_swap", "reschedule"]}},
            "required": ["task_no", "action"]}}},
]


def execute_tool(name: str, args: dict, request_key: str | None = None, principal=None) -> dict:
    """统一入口：参数容错 + 异常兜底，永不抛给 LLM 侧。"""
    fn = _HANDLERS.get(name)
    if fn is None:
        return _err(f"未知工具 {name}")
    try:
        if not isinstance(args, dict):
            return _err("工具参数必须为对象")
        writes={"create_patrol_task","cancel_task","decide_blocked","decide_low_battery"}
        if principal is not None:
            from ..auth import validate_principal
            current=validate_principal(principal,operate=name in writes)
            if name in writes:args={**args,"actor":current['username']}
        if name == "create_patrol_task":
            args = {**args, "request_key": hashlib.sha256(request_key.encode()).hexdigest() if request_key else None}
        return fn(**args)
    except TypeError as e:
        return _err(f"参数错误：{e}")
    except Exception as e:  # noqa: BLE001 工具内部异常转结果文本，保证会话不中断
        return _err(f"工具执行失败：{e}")


_CONFIRM_LABELS = {
    "cancel_task": lambda a: f"确认取消任务 {a.get('task_no')}？取消后机器人将停止执行。",
    "decide_blocked": lambda a: (f"确认对任务 {a.get('task_no')} 执行"
        f"{'重试阻塞点' if a.get('action') == 'retry' else '跳过阻塞点继续巡检'}？"),
    "decide_low_battery": lambda a: (f"确认对任务 {a.get('task_no')} 执行"
        f"{'换电池放行（将核实电量）' if a.get('action') == 'manual_swap' else '取消改期'}？"),
}


def confirm_question(name: str, args: dict) -> str:
    label = _CONFIRM_LABELS.get(name)
    return label(args) if label else f"确认执行 {name}({args})？"
