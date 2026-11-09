"""续航评估：确定性计算（非 LLM），任务创建时的强制前置校验（设计文档 5.2）。

模型：直线里程近似（当前位置→各点→最近桩）× 每米耗电 + 点位数 × 作业耗电，
再乘 (1 + 安全余量%) 得到需求电量，对比当前电量给出三档结论：
  feasible      电量够跑完全程并回桩
  can_reach_dock 跑不完但够回桩 → 任务转 DEFERRED 先充电
  cannot_move   连桩都够不到 → 任务保持 CREATED，等人工换电池
"""
import math

from . import db


def get_cfg() -> dict:
    """读系统配置表并转数值。"""
    rows = db.query("SELECT cfg_key, cfg_value FROM config")
    return {r["cfg_key"]: float(r["cfg_value"]) for r in rows}


def _dist(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.hypot(x2 - x1, y2 - y1)


def assess(points: list[dict], pos_x: float, pos_y: float, battery: float) -> dict:
    """points: [{x, y}, ...] 按执行顺序。返回含明细的评估结果。"""
    cfg = get_cfg()
    per_m = cfg["energy_per_meter_pct"]
    per_pt = cfg["energy_per_point_pct"]
    margin = cfg["safety_margin_pct"] / 100.0
    dock_x, dock_y = cfg["dock_x"], cfg["dock_y"]

    # 全程里程：当前位置→逐点→充电桩
    route_m = 0.0
    cx, cy = pos_x, pos_y
    for p in points:
        route_m += _dist(cx, cy, p["x"], p["y"])
        cx, cy = p["x"], p["y"]
    route_m += _dist(cx, cy, dock_x, dock_y)

    need_pct = (route_m * per_m + len(points) * per_pt) * (1 + margin)

    # 够不够到桩
    to_dock_pct = _dist(pos_x, pos_y, dock_x, dock_y) * per_m * (1 + margin)

    if battery >= need_pct:
        verdict = "feasible"
    elif battery >= to_dock_pct:
        verdict = "can_reach_dock"
    else:
        verdict = "cannot_move"

    return {
        "verdict": verdict,
        "route_m": round(route_m, 2),
        "need_pct": round(need_pct, 2),
        "to_dock_pct": round(to_dock_pct, 2),
        "battery": battery,
    }
