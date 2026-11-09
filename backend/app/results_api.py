"""结果查询接口（设计文档 5.2）：task_event 的 result_received 元数据
LEFT JOIN inspection_result（M5 VLM 结论），并提供巡检图片静态访问。"""
import json
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from . import config, db

router = APIRouter(prefix="/api/results")


@router.get("")
def list_results(task_no: str | None = None, place: str | None = None, limit: int = 200, equipment_id: str | None = None):
    sql = ("SELECT te.point_seq, te.detail, te.created_at, t.task_no, t.robot_id, "
           "ir.normal, ir.reading, ir.anomaly, ir.error, ir.vlm_model, ir.raw, ar.alert_id, vj.status AS analysis_status, vj.attempts AS analysis_attempts, vj.last_error AS analysis_error, vj.available_at AS analysis_next_at "
           "FROM task_event te "
           "JOIN task t ON te.task_id = t.id "
           "LEFT JOIN inspection_result ir "
           "ON ir.task_no = t.task_no AND ir.point_seq = te.point_seq "
           "LEFT JOIN alert_result ar ON ar.task_no=t.task_no AND ar.point_seq=te.point_seq "
           "LEFT JOIN vision_job vj ON vj.task_no=t.task_no AND vj.point_seq=te.point_seq "
           "WHERE te.event = 'result_received'")
    params: dict = {}
    if task_no:
        sql += " AND t.task_no=:n"
        params["n"] = task_no
    sql += " ORDER BY te.id DESC LIMIT :l"
    params["l"] = min(limit, 500)
    rows = db.query(sql, params)
    out = []
    for row in rows:
        detail = {}
        if row.get("detail"):
            try:
                detail = json.loads(row["detail"])
            except json.JSONDecodeError:
                pass
        raw = json.loads(row["raw"]) if isinstance(row.get("raw"),str) else (row.get("raw") or {})
        inspection = detail.get("inspection") or {}
        item = {"task_no": row["task_no"], "robot_id": row["robot_id"],
                "alert_id": row["alert_id"], "point_seq": row["point_seq"], "created_at": row["created_at"],
                "place": detail.get("place", ""),
                "image_url": detail.get("image_url"),
                "captured_at": detail.get("captured_at"),
                "normal": row["normal"], "reading": row["reading"] or "",
                "anomaly": row["anomaly"] or "", "error": row["error"] or "",
                "vlm_model": row["vlm_model"] or "",
                "image_source": detail.get("image_source", "legacy"),
                "capture_error": detail.get("capture_error", ""),
                "camera_stamp_ns": detail.get("camera_stamp_ns"),
                "inspection":inspection, "equipment_id":inspection.get("equipment_id"),
                "equipment_name":inspection.get("equipment_name"),
                "observation_name":inspection.get("observation_name"),
                "checks":raw.get("checks",[]),
                "analysis_status":("retrying" if row["analysis_status"]=="pending" and row["analysis_attempts"] else row["analysis_status"]),
                "analysis_attempts":row["analysis_attempts"] or 0, "analysis_max_attempts":3,
                "analysis_error":row["analysis_error"] or "", "analysis_next_at":row["analysis_next_at"],
                "review_required":bool(row.get("raw") and row["normal"] is None and not row["error"])}
        if place and item["place"] != place:
            continue
        if equipment_id and item["equipment_id"] != equipment_id:
            continue
        out.append(item)
    return out


@router.get("/img/{name}")
def get_image(name: str):
    """巡检图片静态访问（snapshot_node 写入 RESULTS_DIR）。只允许纯文件名，防目录穿越。"""
    if not re.fullmatch(r"[\w.\-]+", name) or ".." in name:
        raise HTTPException(status_code=400, detail="非法图片名")
    path = (config.RESULTS_DIR / name).resolve()
    if not str(path).startswith(str(config.RESULTS_DIR.resolve())) or not path.is_file():
        raise HTTPException(status_code=404, detail="图片不存在")
    return FileResponse(path)
