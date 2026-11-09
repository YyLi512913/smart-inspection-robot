"""Observation-scoped alerts, append-only history and transactional reinspection."""
import json
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from . import db, equipment, state_machine as sm

router = APIRouter(prefix="/api/alerts")
CLOSED = {"closed", "false_positive"}

def event(conn, aid, action, actor="system", note="", task_no=None):
    conn.execute(db.text("INSERT INTO alert_event(alert_id,action,actor,note,task_no) VALUES(:a,:x,:u,:n,:t)"),
                 {"a":aid,"x":action,"u":actor,"n":note[:1000],"t":task_no})

def locked(conn, aid):
    # Always acquire slot before alert, matching assessment ingestion lock order.
    row = conn.execute(db.text("SELECT observation_id FROM inspection_alert WHERE id=:a"),{"a":aid}).mappings().first()
    if not row: raise HTTPException(404,"告警不存在")
    conn.execute(db.text("SELECT * FROM alert_slot WHERE observation_id=:o FOR UPDATE"),{"o":row["observation_id"]}).first()
    return dict(conn.execute(db.text("SELECT * FROM inspection_alert WHERE id=:a FOR UPDATE"),{"a":aid}).mappings().one())

def ingest(conn, task_no, seq, snapshot, result):
    if not snapshot or not snapshot.get("equipment_id"): return
    oid=snapshot["observation_id"]
    conn.execute(db.text("INSERT IGNORE INTO alert_slot(observation_id) VALUES(:o)"),{"o":oid})
    slot=conn.execute(db.text("SELECT * FROM alert_slot WHERE observation_id=:o FOR UPDATE"),{"o":oid}).mappings().one()
    seen=conn.execute(db.text("SELECT 1 FROM alert_result WHERE task_no=:t AND point_seq=:s"),{"t":task_no,"s":seq}).first()
    if seen:return
    conn.execute(db.text("INSERT INTO alert_result(task_no,point_seq) VALUES(:t,:s)"),{"t":task_no,"s":seq})
    alert=None
    if slot["alert_id"]:
        alert=conn.execute(db.text("SELECT * FROM inspection_alert WHERE id=:a FOR UPDATE"),{"a":slot["alert_id"]}).mappings().first()
    recheck=conn.execute(db.text("SELECT * FROM alert_recheck WHERE task_no=:t FOR UPDATE"),{"t":task_no}).mappings().first()
    normal=result.get("normal") is True or result.get("normal")==1
    if not normal:
        kind="abnormal" if result.get("normal") is False or result.get("normal")==0 else "review"
        summary=(result.get("anomaly") or result.get("error") or "图像无法完整判定，需要人工复核")[:500]
        if not alert or alert["status"] in CLOSED:
            aid=conn.execute(db.text("INSERT INTO inspection_alert(equipment_id,equipment_name,observation_id,observation_name,kind,latest_task_no,latest_point_seq,summary) VALUES(:e,:n,:o,:p,:k,:t,:s,:m)"),
                {"e":snapshot["equipment_id"],"n":snapshot["equipment_name"],"o":oid,"p":snapshot["observation_name"],"k":kind,"t":task_no,"s":seq,"m":summary}).lastrowid
            conn.execute(db.text("UPDATE alert_slot SET alert_id=:a WHERE observation_id=:o"),{"a":aid,"o":oid})
            event(conn,aid,"opened",note=summary,task_no=task_no)
        else:
            aid=alert["id"]
            event(conn,aid,"observed",note=summary,task_no=task_no)
        conn.execute(db.text("UPDATE inspection_alert SET occurrences=occurrences+1,kind=IF(kind='abnormal','abnormal',:k),latest_task_no=:t,latest_point_seq=:s,summary=:m,updated_at=NOW(3) WHERE id=:a"),
                     {"a":aid,"k":kind,"t":task_no,"s":seq,"m":summary})
        conn.execute(db.text("UPDATE alert_result SET alert_id=:a WHERE task_no=:t AND point_seq=:s"),{"a":aid,"t":task_no,"s":seq})
    if recheck and recheck["outcome"]=="pending":
        conn.execute(db.text("UPDATE alert_result SET alert_id=:a WHERE task_no=:t AND point_seq=:s"),{"a":recheck["alert_id"],"t":task_no,"s":seq})
        target=conn.execute(db.text("SELECT * FROM inspection_alert WHERE id=:a FOR UPDATE"),{"a":recheck["alert_id"]}).mappings().one()
        # A valid recheck requires every frozen check, with no missing/unreadable entries.
        expected={c["code"] for c in snapshot.get("checks",[])}
        checks=(result.get("raw") or {}).get("checks",[])
        actual={c.get("code"):c.get("status") for c in checks}
        complete=bool(expected) and all(actual.get(c)=="normal" for c in expected)
        task_state=conn.execute(db.text("SELECT status FROM task WHERE task_no=:t"),{"t":task_no}).scalar_one()
        valid=normal and complete and target["occurrences"]==recheck["baseline"] and target["status"]=="rechecking"
        can_close=valid and task_state=="DONE"
        awaiting=valid and task_state not in sm.TERMINAL
        outcome="passed" if can_close else "awaiting_task" if awaiting else "failed"
        conn.execute(db.text("UPDATE alert_recheck SET outcome=:x WHERE id=:i"),{"x":outcome,"i":recheck["id"]})
        if target["status"] not in CLOSED and not awaiting:
            status="closed" if can_close else "confirmed"
            conn.execute(db.text("UPDATE inspection_alert SET status=:s,closed_at=IF(:s='closed',NOW(3),NULL),updated_at=NOW(3) WHERE id=:a"),{"s":status,"a":target["id"]})
            event(conn,target["id"],"recheck_passed" if can_close else "recheck_failed",note="逐项正常，复检通过" if can_close else "复检未通过、检查不完整或期间出现新的异常",task_no=task_no)

def settle_rechecks():
    """A normal image may arrive before task_done; wait for successful execution."""
    rows=db.query("SELECT ar.id,ar.alert_id FROM alert_recheck ar JOIN task t ON t.task_no=ar.task_no WHERE ar.outcome IN ('pending','awaiting_task') AND t.status IN ('DONE','CANCELED','TIMEOUT','FAILED')")
    for row in rows:
        with db.transaction() as conn:
            a=locked(conn,row["alert_id"])
            r=conn.execute(db.text("SELECT ar.*,t.status AS task_status FROM alert_recheck ar JOIN task t ON t.task_no=ar.task_no WHERE ar.id=:i FOR UPDATE"),{"i":row["id"]}).mappings().one()
            if r["outcome"] not in {"pending","awaiting_task"}:continue
            if r["task_status"]=="DONE" and r["outcome"]=="pending":continue
            passed=r["task_status"]=="DONE" and r["outcome"]=="awaiting_task" and r["baseline"]==a["occurrences"] and a["status"]=="rechecking"
            conn.execute(db.text("UPDATE alert_recheck SET outcome=:o WHERE id=:i"),{"o":"passed" if passed else "failed","i":r["id"]})
            if a["status"] not in CLOSED:
                conn.execute(db.text("UPDATE inspection_alert SET status=:s,updated_at=NOW(3),closed_at=IF(:s='closed',NOW(3),NULL) WHERE id=:a"),{"s":"closed" if passed else "confirmed","a":a["id"]})
                event(conn,a["id"],"recheck_passed" if passed else "recheck_failed",note="复检任务完成，逐项检查正常" if passed else "复检任务未成功完成或期间出现新异常",task_no=r["task_no"])


def save_assessment(task_no, seq, snapshot, result, sql, params, connection=None):
    from contextlib import nullcontext
    with (nullcontext(connection) if connection is not None else db.transaction()) as conn:
        # Keep result and alert change atomic. Ignore repeat classifier delivery.
        if snapshot and snapshot.get("equipment_id"):
            conn.execute(db.text("INSERT IGNORE INTO alert_slot(observation_id) VALUES(:o)"),{"o":snapshot["observation_id"]})
            conn.execute(db.text("SELECT * FROM alert_slot WHERE observation_id=:o FOR UPDATE"),{"o":snapshot["observation_id"]}).first()
            if conn.execute(db.text("SELECT 1 FROM alert_result WHERE task_no=:t AND point_seq=:s"),{"t":task_no,"s":seq}).first():return
        conn.execute(db.text(sql),params)
        ingest(conn,task_no,seq,snapshot,result)

@router.get("")
def list_alerts(status: str | None=None, equipment_id: str | None=None, limit:int=100):
    sql="SELECT * FROM inspection_alert WHERE 1=1"
    params={"l":max(1,min(limit,500))}
    if status=="active":sql+=" AND status NOT IN ('closed','false_positive')"
    elif status:
        sql+=" AND status=:s";params["s"]=status
    if equipment_id:
        sql+=" AND equipment_id=:e";params["e"]=equipment_id
    return db.query(sql+" ORDER BY updated_at DESC,id DESC LIMIT :l",params)

@router.get("/summary")
def summary():
    rows=db.query("SELECT status,COUNT(*) AS n FROM inspection_alert GROUP BY status")
    counts={r["status"]:r["n"] for r in rows}
    return {"active":sum(n for s,n in counts.items() if s not in CLOSED),"counts":counts}

@router.get("/{aid}")
def detail(aid:int):
    alert=db.query_one("SELECT * FROM inspection_alert WHERE id=:a",{"a":aid})
    if not alert:raise HTTPException(404,"告警不存在")
    alert["events"]=db.query("SELECT * FROM alert_event WHERE alert_id=:a ORDER BY id",{"a":aid})
    alert["rechecks"]=db.query("SELECT ar.*,t.status AS task_status FROM alert_recheck ar JOIN task t ON t.task_no=ar.task_no WHERE ar.alert_id=:a ORDER BY ar.id DESC",{"a":aid})
    from .results_api import list_results
    alert["evidence"]=next((r for r in list_results(task_no=alert["latest_task_no"],limit=500) if r["point_seq"]==alert["latest_point_seq"]),None)
    return alert

class Action(BaseModel):
    action:str=Field(pattern="^(confirm|false_positive|resolve|note)$")
    actor:str=Field(default="巡检操作员",min_length=1,max_length=64)
    note:str=Field(min_length=1,max_length=1000)

@router.post("/{aid}/actions")
def act(aid:int, body:Action, request:Request = None):
    if request is not None:body=body.model_copy(update={"actor":request.state.user["username"]})
    note=body.note.strip()
    if not note or not body.actor.strip():raise HTTPException(422,"操作人和处理说明不能为空")
    with db.transaction() as conn:
        a=locked(conn,aid)
        if a["status"] in CLOSED:raise HTTPException(409,"告警已关闭，历史记录不可修改")
        if a["status"]=="rechecking" and body.action!="note":raise HTTPException(409,"复检进行中，请等待结果或先取消任务")
        if body.action=="resolve" and a["status"] not in {"confirmed","awaiting_recheck"}:raise HTTPException(409,"请先确认异常，再记录处理结果")
        status={"confirm":"confirmed","false_positive":"false_positive","resolve":"awaiting_recheck","note":a["status"]}[body.action]
        conn.execute(db.text("UPDATE inspection_alert SET status=:s,updated_at=NOW(3),closed_at=IF(:s='false_positive',NOW(3),NULL) WHERE id=:a"),{"s":status,"a":aid})
        event(conn,aid,body.action,body.actor.strip(),note)
    return detail(aid)

class Recheck(BaseModel):
    robot_id:str=Field(default="robot1",min_length=1,max_length=32)
    actor:str=Field(default="巡检操作员",min_length=1,max_length=64)

@router.post("/{aid}/recheck")
def recheck(aid:int, body:Recheck, request:Request = None):
    if request is not None:body=body.model_copy(update={"actor":request.state.user["username"]})
    from .tasks_api import _r
    from . import energy_assessor
    with db.transaction() as conn:
        a=locked(conn,aid)
        if a["status"]=="rechecking":
            previous=conn.execute(db.text("SELECT ar.*,t.status AS task_status FROM alert_recheck ar JOIN task t ON t.task_no=ar.task_no WHERE ar.alert_id=:a ORDER BY ar.id DESC LIMIT 1"),{"a":aid}).mappings().one()
            if previous["task_status"] not in sm.TERMINAL or previous["task_status"]=="DONE":
                return {"task_no":previous["task_no"],"replayed":True}
            # Terminal unsuccessful tasks cannot produce an automatic close.
            conn.execute(db.text("UPDATE alert_recheck SET outcome='failed' WHERE id=:i"),{"i":previous["id"]})
            event(conn,aid,"recheck_failed",note="上次复检任务未完成",task_no=previous["task_no"])
        elif a["status"]!="awaiting_recheck":raise HTTPException(409,"请先确认异常并填写处理记录")
        try:
            points=equipment.expand_targets([{"observation_id":a["observation_id"]}])
        except ValueError as exc:raise HTTPException(409,str(exc)) from exc
        # Reinspect against the same frozen rules that triggered this alert.
        snapshot=conn.execute(db.text("SELECT pi.snapshot FROM task_point_inspection pi JOIN task t ON t.id=pi.task_id WHERE t.task_no=:t AND pi.seq=:s"),{"t":a["latest_task_no"],"s":a["latest_point_seq"]}).scalar_one()
        points[0]["inspection"]=equipment.decode(snapshot)
        points[0]["check_item"]="；".join(c["name"] for c in points[0]["inspection"]["checks"])[:128]
        points[0]["inspection"]["yaw"]=equipment.expand_targets([{"observation_id":a["observation_id"]}])[0]["inspection"]["yaw"]
        raw=_r.get(f"robot:{body.robot_id}:live")
        if not raw:raise HTTPException(409,"机器人离线，无法创建复检任务")
        live=json.loads(raw)
        if not isinstance(live.get("pos"),dict) or not isinstance(live.get("battery"),(int,float)):raise HTTPException(409,"遥测不完整")
        verdict=energy_assessor.assess(points,live["pos"]["x"],live["pos"]["y"],live["battery"])
        if verdict["verdict"]!="feasible":raise HTTPException(409,"当前电量不足以执行复检，请充电后重试")
        no=sm.create_task(body.robot_id,"manual",5,body.actor,points,verdict,connection=conn)
        conn.execute(db.text("INSERT INTO alert_recheck(alert_id,task_no,baseline) VALUES(:a,:t,:b)"),{"a":aid,"t":no,"b":a["occurrences"]})
        conn.execute(db.text("UPDATE inspection_alert SET status='rechecking',updated_at=NOW(3) WHERE id=:a"),{"a":aid})
        event(conn,aid,"recheck_started",body.actor,task_no=no)
    return {"task_no":no,"replayed":False}
