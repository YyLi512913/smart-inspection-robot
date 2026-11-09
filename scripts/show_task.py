#!/usr/bin/env python3
"""打印指定任务的最新事件:python3 show_task.py <TASK_NO> [N]"""
import json
import sys
import urllib.request

no = sys.argv[1]
n = int(sys.argv[2]) if len(sys.argv) > 2 else 8
d = json.load(urllib.request.urlopen(f"http://127.0.0.1:8000/api/tasks/{no}"))
print("status:", d["status"])
for p in d["points"]:
    print(f"  point {p['seq']} {p['place']}: {p['status']}")
for e in d["events"][-n:]:
    det = json.dumps(e["detail"], ensure_ascii=False)[:220] if e.get("detail") else ""
    print(e["created_at"], e["event"], e.get("point_seq"), det)
