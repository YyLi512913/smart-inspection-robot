import json
from ament_index_python.packages import get_package_share_directory
from pathlib import Path

def load_observations():
    data=json.loads((Path(get_package_share_directory("inspection_robot"))/"config/equipment_catalog.json").read_text())
    return {p["id"]:p for a in data["equipment"] for p in a["observations"]}
