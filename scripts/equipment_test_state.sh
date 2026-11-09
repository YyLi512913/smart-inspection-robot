#!/bin/bash
set -e
source /opt/ros/jazzy/setup.bash
export GZ_PARTITION="${GZ_PARTITION:-inspection-equipment-test}"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "$PROJECT_ROOT/scripts/set_equipment_state.py" "$@"
