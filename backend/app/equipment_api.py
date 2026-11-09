from fastapi import APIRouter, HTTPException
from .equipment import list_equipment

router = APIRouter(prefix="/api/equipment")

@router.get("")
def equipment_list(area: str | None = None):
    return list_equipment(area=area)

@router.get("/{equipment_id}")
def equipment_detail(equipment_id: str):
    rows = list_equipment(equipment_id=equipment_id)
    if not rows:
        raise HTTPException(404, "设备不存在或已停用")
    return rows[0]
