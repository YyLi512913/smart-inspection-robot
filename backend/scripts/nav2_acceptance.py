"""Real Gazebo/Nav2 + camera + MQTT + isolated MySQL acceptance test."""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tests"))
from test_reliability import ReliabilityTests
from app import config, db, delivery, equipment, state_machine as sm
from app.mqtt_consumer import MqttConsumer
from acceptance_support import AnalysisWorkers
from app.agent.tools import PLACES

class MemoryTelemetry:
    def __init__(self):self.live={}
    def set(self,key,value,ex=None):self.live[key]=json.loads(value)

def wait(predicate,seconds=240):
    end=time.monotonic()+seconds
    previous=None
    while time.monotonic()<end:
        delivery.flush_pending()
        if predicate():return
        time.sleep(.2)
    raise AssertionError("Condition timed out")

parser=argparse.ArgumentParser()
parser.add_argument("--place",action="append",choices=list(PLACES))
parser.add_argument("--vlm",action="store_true",help="Call configured VLM and verify database results")
args=parser.parse_args()
ReliabilityTests.setUpClass()
consumer=None
workers=AnalysisWorkers(args.vlm)
try:
    config.MQTT_PORT=18885
    config.MQTT_CLIENT_ID="nav2-acceptance-"+__import__("uuid").uuid4().hex
    if args.vlm:
        config.RESULTS_DIR=Path(__file__).resolve().parents[2]/".tmp/nav2-camera"
    consumer=MqttConsumer()
    consumer.r=MemoryTelemetry()
    consumer.start()
    delivery.set_publisher(consumer.publish)
    workers.start()
    wait(consumer.c.is_connected,15)
    selected=args.place or ["corridor_west","workshop"]
    points=equipment.expand_targets([{"place":p,"check_item":"设备与通道外观"} for p in selected])
    no=sm.create_task("nav2test","manual",5,"nav2_acceptance",points,{"verdict":"feasible"})
    print("TASK "+no,flush=True)
    last=None
    def completed():
        global last
        t=db.query_one("SELECT status FROM task WHERE task_no=:n",{"n":no})
        live=consumer.r.live.get("robot:nav2test:live",{})
        state=t["status"]
        if state!=last:
            print(json.dumps({"status":state,"pos":live.get("pos")},ensure_ascii=False),flush=True)
            last=state
        if state in {"BLOCKED","FAILED","TIMEOUT"}:
            raise AssertionError(f"Patrol did not complete: {state}")
        return state=="DONE"
    wait(completed,300)
    wait(lambda:len(db.query("SELECT te.id FROM task_event te JOIN task t ON t.id=te.task_id WHERE t.task_no=:n AND te.event='result_received'",{"n":no}))==len(points),30)
    events=db.query("SELECT te.detail FROM task_event te JOIN task t ON t.id=te.task_id WHERE t.task_no=:n AND te.event='result_received' ORDER BY te.id",{"n":no})
    images=[json.loads(e["detail"]) for e in events]
    assert all(e["image_source"]=="camera" and e["image_url"] and not e["capture_error"] for e in images),images
    for image in images:
        path=Path(__file__).resolve().parents[2]/".tmp/nav2-camera"/image["image_url"].split("/")[-1]
        assert path.is_file() and path.stat().st_size>1000,path
    vlm_results=[]
    if args.vlm:
        wait(lambda:len(db.query("SELECT id FROM inspection_result WHERE task_no=:n",{"n":no}))==len(points),220)
        vlm_results=db.query("SELECT normal,reading,anomaly,error FROM inspection_result WHERE task_no=:n ORDER BY point_seq",{"n":no})
        assert all(r["normal"] is not None and not r["error"] for r in vlm_results),vlm_results
    cancel_no=sm.create_task("nav2test","manual",5,"nav2_cancel",
        [{"place":"server_room","x":21.75,"y":-11.0,"check_item":"取消验证"}],{"verdict":"feasible"})
    wait(lambda:db.query_one("SELECT status FROM task WHERE task_no=:n",{"n":cancel_no})["status"]=="NAVIGATING",30)
    time.sleep(3)
    sm.cancel(cancel_no)
    wait(lambda:db.query_one("SELECT task_no FROM robot_execution WHERE robot_id='nav2test'")["task_no"] is None,45)
    assert db.query_one("SELECT status FROM mqtt_command WHERE task_no=:n AND action='cancel'",{"n":cancel_no})["status"]=="acked"
    result={"navigation":"Nav2 NavigateToPose","patrol_completed":True,"real_images":images,
            "cancel_during_navigation":True,"database_isolated":True,"vlm_calls_suppressed_for_test":not args.vlm,"vlm_results":vlm_results}
    (Path(__file__).resolve().parents[2]/".tmp/nav2-acceptance-result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print("RESULT "+json.dumps(result,ensure_ascii=False),flush=True)
finally:
    if consumer:consumer.stop()
    delivery.set_publisher(None)
    workers.close()
    ReliabilityTests.tearDownClass()
