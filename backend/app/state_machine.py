"""Task state machine: robot ownership, strict transitions and transactional outbox."""
from contextlib import nullcontext
import hashlib
import json
import logging
from datetime import datetime
from uuid import uuid4

from . import db, delivery

logger = logging.getLogger("state_machine")
TERMINAL = {"DONE", "CANCELED", "TIMEOUT", "FAILED"}
RUNNING = {"ACCEPTED", "NAVIGATING", "INSPECTING", "REPORTING"}
EVENT_TRANSITIONS = {
    "accepted": ({"DISPATCHED"}, "ACCEPTED"),
    "navigating": ({"ACCEPTED", "REPORTING", "INSPECTING"}, "NAVIGATING"),
    "point_arrived": ({"ACCEPTED", "NAVIGATING"}, "INSPECTING"),
    "blocked": (RUNNING, "BLOCKED"),
    "stuck": (RUNNING, "BLOCKED"),
    "task_done": (RUNNING, "DONE"),
    "task_failed": (RUNNING | {"DISPATCHED"}, "FAILED"),
}


class TaskConflict(ValueError):
    """Business conflict; exposed as HTTP 409 / tool error."""


def set_publisher(fn):
    delivery.set_publisher(fn)


def audit(conn, task_id, robot_id, event, point_seq=None, detail=None):
    conn.execute(db.text(
        "INSERT INTO task_event (task_id, robot_id, event, point_seq, detail) "
        "VALUES (:t, :r, :e, :p, :d)"),
        {"t": task_id, "r": robot_id, "e": event, "p": point_seq,
         "d": json.dumps(detail, ensure_ascii=False) if detail is not None else None})


def next_task_no():
    # No COUNT()+1 race, even when multiple robots create concurrently.
    return f"T{datetime.now():%Y%m%d}-{uuid4().hex[:20]}"


def _lock_robot(conn, robot_id):
    conn.execute(db.text("INSERT IGNORE INTO robot_execution (robot_id) VALUES (:r)"),
                 {"r": robot_id})
    return conn.execute(db.text(
        "SELECT * FROM robot_execution WHERE robot_id=:r FOR UPDATE"),
        {"r": robot_id}).mappings().first()


def _get_task(conn, task_no):
    return conn.execute(db.text("SELECT * FROM task WHERE task_no=:n FOR UPDATE"),
                        {"n": task_no}).mappings().first()


def _locked_task(conn, task_no):
    row = conn.execute(db.text("SELECT robot_id FROM task WHERE task_no=:n"),
                       {"n": task_no}).mappings().first()
    if row is None:
        return None
    _lock_robot(conn, row["robot_id"])
    return _get_task(conn, task_no)


def _claim(conn, task):
    slot = _lock_robot(conn, task["robot_id"])
    if slot["task_no"] and slot["task_no"] != task["task_no"]:
        raise TaskConflict(f"{task['robot_id']} 被任务 {slot['task_no']} 占用，尚未确认释放")
    # Also protect installations upgraded with legacy, nonterminal tasks.
    other = conn.execute(db.text(
        "SELECT task_no FROM task WHERE robot_id=:r AND task_no<>:n "
        "AND status NOT IN ('DONE','CANCELED','TIMEOUT','FAILED') LIMIT 1"),
        {"r": task["robot_id"], "n": task["task_no"]}).mappings().first()
    if other:
        raise TaskConflict(f"{task['robot_id']} 已有未结束任务 {other['task_no']}")
    conn.execute(db.text("UPDATE robot_execution SET task_no=:n WHERE robot_id=:r"),
                 {"n": task["task_no"], "r": task["robot_id"]})


def _release(conn, task):
    conn.execute(db.text(
        "UPDATE robot_execution SET task_no=NULL WHERE robot_id=:r AND task_no=:n"),
        {"r": task["robot_id"], "n": task["task_no"]})


def _points_for_publish(conn, task_id):
    rows = conn.execute(db.text(
        "SELECT p.seq,p.place,p.x,p.y,p.check_item,m.snapshot FROM task_point p "
        "LEFT JOIN task_point_inspection m ON m.task_id=p.task_id AND m.seq=p.seq "
        "WHERE p.task_id=:t AND p.status='pending' ORDER BY p.seq"), {"t":task_id}).mappings().all()
    out=[]
    for row in rows:
        point={"seq":row["seq"],"place":row["place"],"x":row["x"],"y":row["y"],"check":row["check_item"]}
        if row["snapshot"]:
            snapshot=json.loads(row["snapshot"]) if isinstance(row["snapshot"],str) else row["snapshot"]
            point.update(inspection=snapshot,yaw=snapshot["yaw"])
        out.append(point)
    return out


def dispatch(conn, task, audit_event, detail=None):
    _claim(conn, task)
    points = _points_for_publish(conn, task["id"])
    if not points:
        raise TaskConflict("没有待执行点位，无法下发")
    delivery.supersede(conn, task["task_no"])
    command_id = delivery.enqueue(conn, task["robot_id"], task["task_no"], "patrol", points)
    conn.execute(db.text(
        "UPDATE task SET status='DISPATCHED', dispatched_at=NOW(3) WHERE id=:i"),
        {"i": task["id"]})
    audit(conn, task["id"], task["robot_id"], audit_event,
          detail={**(detail or {}), "command_id": command_id})


def create_task(robot_id, source, priority, created_by, points, verdict, request_key=None, request_identity=None, connection=None):
    if not 1 <= priority <= 9:
        raise ValueError("至少一个点位，优先级必须在 1~9")
    fingerprint = hashlib.sha256(json.dumps(
        [robot_id, source, priority, created_by, request_identity if request_identity is not None else points], sort_keys=True,
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    with (nullcontext(connection) if connection is not None else db.transaction()) as conn:
        if request_key:
            if not 1 <= len(request_key) <= 128:
                raise ValueError("Idempotency-Key 长度必须在 1~128")
            conn.execute(db.text(
                "INSERT IGNORE INTO task_request (request_key, request_hash) VALUES (:k,:h)"),
                {"k": request_key, "h": fingerprint})
            req = conn.execute(db.text(
                "SELECT * FROM task_request WHERE request_key=:k FOR UPDATE"),
                {"k": request_key}).mappings().first()
            if req["request_hash"] != fingerprint:
                raise TaskConflict("相同 Idempotency-Key 不能用于不同任务参数")
            if req["task_no"]:
                return req["task_no"]
        if not points:
            raise ValueError("至少一个点位")
        task_no = next_task_no()
        _claim(conn, {"robot_id": robot_id, "task_no": task_no})
        task_id = conn.execute(db.text(
            "INSERT INTO task (task_no, robot_id, source, status, priority, created_by) "
            "VALUES (:n,:r,:s,'CREATED',:p,:u)"),
            {"n": task_no, "r": robot_id, "s": source, "p": priority, "u": created_by}).lastrowid
        for seq, point in enumerate(points, 1):
            conn.execute(db.text(
                "INSERT INTO task_point (task_id,seq,place,x,y,check_item) VALUES (:t,:s,:p,:x,:y,:c)"),
                {"t": task_id, "s": seq, "p": point["place"], "x": point["x"],
                 "y": point["y"], "c": point.get("check_item", "")})
            if point.get("inspection"):
                conn.execute(db.text(
                    "INSERT INTO task_point_inspection(task_id,seq,snapshot) VALUES(:t,:s,:m)"),
                    {"t":task_id,"s":seq,"m":json.dumps(point["inspection"],ensure_ascii=False)})
        task = {"id": task_id, "task_no": task_no, "robot_id": robot_id}
        audit(conn, task_id, robot_id, "created", detail={"energy": verdict})
        if verdict["verdict"] == "feasible":
            dispatch(conn, task, "dispatch", {"energy": verdict})
        elif verdict["verdict"] == "can_reach_dock":
            conn.execute(db.text("UPDATE task SET status='DEFERRED' WHERE id=:i"), {"i": task_id})
            audit(conn, task_id, robot_id, "deferred", detail={"energy": verdict})
        else:
            audit(conn, task_id, robot_id, "low_battery", detail={"energy": verdict})
        if request_key:
            conn.execute(db.text("UPDATE task_request SET task_no=:n WHERE request_key=:k"),
                         {"n": task_no, "k": request_key})
    return task_no


def _incoming(conn, robot_id, payload):
    task = _locked_task(conn, payload.get("task_id"))
    if task is None or task["robot_id"] != robot_id:
        return None, None
    cmd = delivery.command_for(conn, task["task_no"], robot_id, payload.get("command_id"))
    event_id = payload.get("event_id")
    if cmd is None or not isinstance(event_id, str) or not 1 <= len(event_id) <= 64:
        return None, None
    # The task row serializes duplicate detection with updates and audit insertion.
    existing = conn.execute(db.text("SELECT event_id FROM message_inbox WHERE event_id=:e"),
                            {"e": event_id}).first()
    if existing:
        return None, None
    conn.execute(db.text(
        "INSERT INTO message_inbox (event_id,robot_id,task_no) VALUES (:e,:r,:n)"),
        {"e": event_id, "r": robot_id, "n": task["task_no"]})
    return task, cmd


def handle_event(robot_id, payload):
    event = payload.get("event")
    with db.transaction() as conn:
        task, cmd = _incoming(conn, robot_id, payload)
        if task is None:
            return
        if cmd["status"] == "superseded":
            return
        detail = {**(payload.get("detail") or {}), "command_id": cmd["command_id"]}
        if event == "cancel_confirmed" and cmd["action"] == "cancel":
            delivery.acknowledge(conn, cmd["command_id"])
            audit(conn, task["id"], robot_id, event, detail=detail)
            _release(conn, task)
            return
        if cmd["action"] != "patrol" or task["status"] in TERMINAL:
            return
        if event == "recovery_required":
            delivery.acknowledge(conn, cmd["command_id"])
            conn.execute(db.text("UPDATE task SET status='BLOCKED' WHERE id=:i"), {"i": task["id"]})
            audit(conn, task["id"], robot_id, event, detail=detail)
            return
        if event == "command_rejected":
            # Unknown robot-side execution may still be moving. Keep ownership until cancel ACK.
            delivery.acknowledge(conn, cmd["command_id"])
            audit(conn, task["id"], robot_id, event, detail=detail)
            return
        rule = EVENT_TRANSITIONS.get(event)
        if not rule or task["status"] not in rule[0]:
            logger.warning("Rejected transition %s: %s --%s", task["task_no"], task["status"], event)
            return
        seq = payload.get("point_seq")
        point = None
        if event in {"navigating", "point_arrived", "blocked", "stuck"}:
            point = conn.execute(db.text(
                "SELECT * FROM task_point WHERE task_id=:t AND seq=:s"),
                {"t": task["id"], "s": seq}).mappings().first()
            first = conn.execute(db.text(
                "SELECT seq FROM task_point WHERE task_id=:t "
                "AND status IN ('pending','navigating','blocked') ORDER BY seq LIMIT 1"),
                {"t": task["id"]}).mappings().first()
            if not point or not first or first["seq"] != seq or point["status"] in {"skipped", "inspected", "arrived"}:
                return
        if event == "task_done":
            remain = conn.execute(db.text(
                "SELECT seq FROM task_point WHERE task_id=:t "
                "AND status IN ('pending','navigating','blocked') LIMIT 1"),
                {"t": task["id"]}).first()
            if remain:
                return
        delivery.acknowledge(conn, cmd["command_id"])
        target = rule[1]
        conn.execute(db.text(
            "UPDATE task SET status=:s, started_at=COALESCE(started_at,NOW(3)) WHERE id=:i"),
            {"s": target, "i": task["id"]})
        point_state = {"navigating": "navigating", "point_arrived": "arrived",
                       "blocked": "blocked", "stuck": "blocked"}.get(event)
        if point_state:
            conn.execute(db.text("UPDATE task_point SET status=:s WHERE task_id=:t AND seq=:q"),
                         {"s": point_state, "t": task["id"], "q": seq})
        if target in TERMINAL:
            conn.execute(db.text("UPDATE task SET finished_at=NOW(3) WHERE id=:i"), {"i": task["id"]})
            _release(conn, task)
        audit(conn, task["id"], robot_id, event, seq, detail)


def handle_result(robot_id, payload):
    with db.transaction() as conn:
        task, cmd = _incoming(conn, robot_id, payload)
        if task is None or cmd["action"] != "patrol":
            return
        seq = payload.get("point_seq")
        point = conn.execute(db.text(
            "SELECT * FROM task_point WHERE task_id=:t AND seq=:s"),
            {"t": task["id"], "s": seq}).mappings().first()
        # Late photos are valid after DONE; never revive skipped/pending points.
        if not point or point["status"] not in {"arrived", "inspected"}:
            return
        arrival = conn.execute(db.text(
            "SELECT detail FROM task_event WHERE task_id=:t AND point_seq=:s "
            "AND event='point_arrived' ORDER BY id DESC LIMIT 1"),
            {"t": task["id"], "s": seq}).mappings().first()
        arrival_detail = json.loads(arrival["detail"]) if arrival and isinstance(arrival["detail"], str) else (arrival["detail"] if arrival else {})
        if arrival_detail.get("command_id") != cmd["command_id"]:
            return
        existing = conn.execute(db.text(
            "SELECT id FROM task_event WHERE task_id=:t AND point_seq=:s AND event='result_received'"),
            {"t": task["id"], "s": seq}).first()
        if existing:
            return
        if payload.get("place") != point["place"]:
            return
        conn.execute(db.text("UPDATE task_point SET status='inspected' WHERE task_id=:t AND seq=:s"),
                     {"t": task["id"], "s": seq})
        meta=conn.execute(db.text(
            "SELECT snapshot FROM task_point_inspection WHERE task_id=:t AND seq=:s"),
            {"t":task["id"],"s":seq}).mappings().first()
        inspection=(json.loads(meta["snapshot"]) if isinstance(meta["snapshot"],str) else meta["snapshot"]) if meta else None
        payload={**payload}
        if inspection:
            payload["inspection"]=inspection
        audit(conn, task["id"], robot_id, "result_received", seq, payload)
        if not payload.get("image_url"):
            error = str(payload.get("capture_error") or "未收到巡检图像")[:255]
            conn.execute(db.text(
                "INSERT INTO inspection_result (task_no,point_seq,place,check_item,error) "
                "VALUES (:n,:s,:p,:c,:e) ON DUPLICATE KEY UPDATE error=VALUES(error)"),
                {"n":task["task_no"],"s":seq,"p":point["place"],"c":point["check_item"],"e":error})
            from .alerts import ingest
            ingest(conn, task["task_no"], seq, inspection, {"normal":None,"error":error,"raw":None})
        if payload.get("image_url"):
            from .vision_jobs import enqueue
            enqueue(conn, task["task_no"], seq, point["place"], point["check_item"], payload["image_url"], inspection)


def _cancel_command(conn, task):
    delivery.supersede(conn, task["task_no"])
    return delivery.enqueue(conn, task["robot_id"], task["task_no"], "cancel")


def cancel(task_no, operator=""):
    with db.transaction() as conn:
        task = _locked_task(conn, task_no)
        if task is None:
            return False
        if task["status"] in TERMINAL:
            # Retry transport for an already-requested cancellation without duplicating commands.
            return task["status"] == "CANCELED"
        slot = _lock_robot(conn, task["robot_id"])
        owns = slot["task_no"] == task_no
        conn.execute(db.text("UPDATE task SET status='CANCELED', finished_at=NOW(3) WHERE id=:i"),
                     {"i": task["id"]})
        audit(conn, task["id"], task["robot_id"], "cancel", detail={"operator": operator, "awaiting_robot": owns})
        if owns:
            _cancel_command(conn, task)
    return True


def decision(task_no, action, operator=""):
    if action not in {"retry", "skip"}:
        raise ValueError("未知决策")
    with db.transaction() as conn:
        task = _locked_task(conn, task_no)
        if task is None or task["status"] != "BLOCKED":
            return None
        blocked = conn.execute(db.text(
            "SELECT seq FROM task_point WHERE task_id=:t AND status='blocked'"),
            {"t": task["id"]}).first()
        if not blocked:
            raise TaskConflict("执行节点需要恢复核对，请先取消任务，确认机器人停止后再创建任务")
        conn.execute(db.text(
            "UPDATE task_point SET status=:s WHERE task_id=:t AND status='blocked'"),
            {"s": "pending" if action == "retry" else "skipped", "t": task["id"]})
        audit(conn, task["id"], task["robot_id"], f"decision_{action}", detail={"operator": operator})
        if _points_for_publish(conn, task["id"]):
            dispatch(conn, task, "decision_dispatch")
            return "DISPATCHED"
        conn.execute(db.text("UPDATE task SET status='DONE', finished_at=NOW(3) WHERE id=:i"), {"i": task["id"]})
        _release(conn, task)
        return "DONE"


def try_resume_deferred(robot_id, battery, pos=None):
    from . import energy_assessor
    if not pos or battery < energy_assessor.get_cfg()["resume_threshold_pct"]:
        return
    with db.transaction() as conn:
        slot = _lock_robot(conn, robot_id)
        if not slot["task_no"]:
            return
        task = _get_task(conn, slot["task_no"])
        if not task or task["status"] != "DEFERRED":
            return
        verdict = energy_assessor.assess(_points_for_publish(conn, task["id"]), pos["x"], pos["y"], battery)
        if verdict["verdict"] == "feasible":
            dispatch(conn, task, "deferred_resume", {"energy": verdict})


def check_timeouts():
    from . import energy_assessor
    seconds = int(energy_assessor.get_cfg()["watchdog_timeout_s"])
    rows = db.query(
        "SELECT task_no FROM task t WHERE status IN ('ACCEPTED','NAVIGATING','INSPECTING','REPORTING','DISPATCHED') "
        "AND (SELECT MAX(created_at) FROM task_event WHERE task_id=t.id) "
        "< NOW(3) - INTERVAL :s SECOND", {"s": seconds})
    timed_out = []
    for row in rows:
        with db.transaction() as conn:
            task = _locked_task(conn, row["task_no"])
            # Recheck under locks: a recent event may have arrived since the scan.
            stale = conn.execute(db.text(
                "SELECT id FROM task WHERE id=:i AND status IN ('ACCEPTED','NAVIGATING','INSPECTING','REPORTING','DISPATCHED') "
                "AND (SELECT MAX(created_at) FROM task_event WHERE task_id=:i) < NOW(3) - INTERVAL :s SECOND"),
                {"i": task["id"], "s": seconds}).first() if task else None
            if not stale:
                continue
            conn.execute(db.text("UPDATE task SET status='TIMEOUT',finished_at=NOW(3) WHERE id=:i"), {"i": task["id"]})
            command_id = _cancel_command(conn, task)
            audit(conn, task["id"], task["robot_id"], "timeout",
                  detail={"timeout_s": seconds, "cancel_command_id": command_id, "awaiting_robot": True})
            # Ownership stays held until the robot has actually confirmed cancellation.
            timed_out.append(task["task_no"])
    return timed_out


def _vlm_job(task_no: str, seq, place: str, check_item: str, image_url: str, inspection=None):
    """Synchronous compatibility helper for isolated validation scripts."""
    from . import vision_jobs
    job={"task_no":task_no,"point_seq":seq,"place":place,"check_item":check_item,
         "image_url":image_url,"snapshot":inspection}
    res=vision_jobs.detect(job)
    with db.transaction() as conn:
        vision_jobs.persist_assessment(conn,job,res)
