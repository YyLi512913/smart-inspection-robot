"""任务 REST 接口（设计文档 5.2）：创建+续航硬校验+下发、查询、取消、blocked 决策。"""
import json
import logging
import math

import redis
from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel, Field, model_validator

from . import config, db, energy_assessor, state_machine, equipment

logger = logging.getLogger("tasks_api")
router = APIRouter(prefix="/api/tasks")

_r = redis.Redis.from_url(config.REDIS_URL, decode_responses=True)


class PointIn(BaseModel):
    place: str
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    check_item: str = Field(default="", max_length=128)

    @model_validator(mode="after")
    def validate_location(self):
        from .agent.tools import PLACES
        if self.place not in PLACES:
            raise ValueError("地点不在可巡检地点枚举内")
        x, y, _ = PLACES[self.place]
        if math.hypot(self.x-x, self.y-y) > 0.05:
            raise ValueError("地点与坐标不一致，请使用系统点位坐标")
        return self


class TaskCreate(BaseModel):
    robot_id: str = Field(min_length=1, max_length=32)
    source: str = Field(default="manual", pattern="^(chat|scheduled|manual)$")
    priority: int = Field(default=5, ge=1, le=9)
    created_by: str = Field(default="", max_length=32)
    points: list[PointIn] | None = Field(default=None, min_length=1, max_length=100)
    targets: list[dict] | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def one_route(self):
        if (self.points is None) == (self.targets is None):
            raise ValueError("points 和 targets 必须且只能提供一种")
        return self


class DecisionIn(BaseModel):
    action: str = Field(pattern="^(retry|skip)$")


def _task_detail(task_no: str) -> dict:
    task = db.query_one("SELECT * FROM task WHERE task_no=:n", {"n": task_no})
    if task is None:
        raise HTTPException(404, f"任务 {task_no} 不存在")
    task["points"] = db.query(
        "SELECT seq, place, x, y, check_item, status FROM task_point "
        "WHERE task_id=:t ORDER BY seq", {"t": task["id"]}
    )
    snapshots=db.query("SELECT seq,snapshot FROM task_point_inspection WHERE task_id=:t",{"t":task["id"]})
    metadata={row["seq"]:equipment.decode(row["snapshot"]) for row in snapshots}
    for point in task["points"]:
        point["inspection"]=metadata.get(point["seq"])
    task["events"] = db.query(
        "SELECT event, point_seq, detail, created_at FROM task_event "
        "WHERE task_id=:t ORDER BY id", {"t": task["id"]}
    )
    for e in task["events"]:
        if isinstance(e.get("detail"), str):
            try:
                e["detail"] = json.loads(e["detail"])
            except json.JSONDecodeError:
                pass
    task["delivery"] = db.query("SELECT command_id, action, status, attempts, last_sent_at, created_at, acked_at FROM mqtt_command WHERE task_no=:n ORDER BY created_at", {"n": task_no})
    slot = db.query_one("SELECT task_no FROM robot_execution WHERE robot_id=:r", {"r": task["robot_id"]})
    task["execution_held"] = bool(slot and slot["task_no"] == task_no)
    return task


@router.post("")
def create_task(body: TaskCreate, idempotency_key: str | None = Header(default=None, max_length=128), request: Request = None):
    """创建任务：强制续航硬校验（Agent 跳过评估也会被这里拦截，设计文档 5.3 护栏）。"""
    if request is not None:
        body=body.model_copy(update={"created_by":request.state.user["username"]})
        if idempotency_key:
            import hashlib
            idempotency_key=hashlib.sha256(f"user:{request.state.user['id']}:{idempotency_key}".encode()).hexdigest()
    identity={"targets":body.targets} if body.targets is not None else {"points":[p.model_dump() for p in body.points]}
    if idempotency_key:
        existing = db.query_one("SELECT task_no FROM task_request WHERE request_key=:k", {"k":idempotency_key})
        if existing and existing["task_no"]:
            task_no=state_machine.create_task(body.robot_id,body.source,body.priority,body.created_by,
                [],{},request_key=idempotency_key,request_identity=identity)
            return {"task":_task_detail(task_no),"energy":None,"replayed":True}
    try:
        planned = equipment.expand_targets(body.targets if body.targets is not None else [
            {"place":p.place,"check_item":p.check_item} for p in body.points])
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc
    raw = _r.get(f"robot:{body.robot_id}:live")
    if raw is None:
        raise HTTPException(409, f"{body.robot_id} 离线：无实时遥测，无法评估续航")
    live = json.loads(raw)
    if not isinstance(live.get("pos"), dict) or not isinstance(live.get("battery"), (int, float)):
        raise HTTPException(409, "设备遥测不完整，无法创建任务")
    verdict = energy_assessor.assess(
        planned,
        live["pos"]["x"], live["pos"]["y"], live["battery"],
    )
    task_no = state_machine.create_task(
        body.robot_id, body.source, body.priority, body.created_by,
        planned, verdict, request_key=idempotency_key, request_identity=identity,
    )
    task = _task_detail(task_no)
    return {"task": task, "energy": verdict}


@router.get("")
def list_tasks(status: str | None = None, robot_id: str | None = None, limit: int = 50):
    sql = "SELECT task_no, robot_id, source, status, priority, created_at, " \
          "dispatched_at, started_at, finished_at FROM task WHERE 1=1"
    params: dict = {}
    if status:
        sql += " AND status=:s"
        params["s"] = status
    if robot_id:
        sql += " AND robot_id=:r"
        params["r"] = robot_id
    sql += " ORDER BY id DESC LIMIT :l"
    params["l"] = max(1, min(limit, 200))
    return db.query(sql, params)


@router.get("/{task_no}")
def get_task(task_no: str):
    return _task_detail(task_no)


@router.post("/{task_no}/cancel")
def cancel_task(task_no: str, request: Request = None):
    if not state_machine.cancel(task_no, operator=request.state.user["username"] if request else ""):
        raise HTTPException(409, f"任务 {task_no} 不存在或已终态，无法取消")
    return _task_detail(task_no)


@router.post("/{task_no}/decision")
def decide_task(task_no: str, body: DecisionIn, request: Request = None):
    target = state_machine.decision(task_no, body.action, operator=request.state.user["username"] if request else "")
    if target is None:
        raise HTTPException(409, f"任务 {task_no} 不处于 BLOCKED，无法决策")
    return _task_detail(task_no)
