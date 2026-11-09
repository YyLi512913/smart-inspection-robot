"""Physical device-state changes -> new camera captures -> task-bound assessments.

Uses ROS domain 215 / MQTT 18886 configured in the test stack, and an isolated MySQL database.
"""
import argparse
import hashlib
import os
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"backend/tests"))
import test_reliability as reliability
from app import config, db, delivery, equipment, state_machine as sm
from app.mqtt_consumer import MqttConsumer
from acceptance_support import AnalysisWorkers

class MemoryTelemetry:
    def __init__(self):self.live={}
    def set(self,key,value,ex=None):self.live[key]=json.loads(value)

def switch(state):
    if os.name=="nt":
        mapped=subprocess.run(["wsl.exe","-d","Ubuntu-24.04","wslpath","-a",ROOT.as_posix()],capture_output=True,text=True,check=True)
        command=["wsl.exe","-d","Ubuntu-24.04","env","GZ_PARTITION=inspection-equipment-test","bash",mapped.stdout.strip()+"/scripts/equipment_test_state.sh","all",state]
    else:
        command=["env","GZ_PARTITION=inspection-equipment-test","bash",str(ROOT/"scripts/equipment_test_state.sh"),"all",state]
    for attempt in range(6):
        result=subprocess.run(command,capture_output=True,text=True,encoding="utf-8",errors="replace")
        if result.returncode==0:return
        if attempt==5:raise RuntimeError(result.stderr or result.stdout)
        time.sleep(1)


def wait(predicate,seconds=240):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        delivery.flush_pending()
        if predicate():return
        time.sleep(.2)
    raise AssertionError("Condition timed out")

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--vlm",action="store_true")
    args=parser.parse_args()
    reliability.ReliabilityTests.setUpClass()
    consumer=None;workers=AnalysisWorkers(args.vlm)
    report={"scenario":"equipment-state-comparison","cases":[],"vlm":args.vlm}
    try:
        equipment.seed_catalog()
        config.MQTT_PORT=18886
        config.MQTT_CLIENT_ID="equipment-acceptance-"+__import__("uuid").uuid4().hex
        config.RESULTS_DIR=ROOT/".tmp/equipment-camera"
        consumer=MqttConsumer();consumer.r=MemoryTelemetry()
        consumer.start();delivery.set_publisher(consumer.publish);workers.start()
        wait(consumer.c.is_connected,20)
        for state in ("normal","abnormal"):
            switch(state)
            route=equipment.expand_targets([{"area":"workshop"}])
            no=sm.create_task("equipmenttest","manual",5,"equipment_acceptance",route,{"verdict":"feasible"})
            print("TASK "+state+" "+no,flush=True)
            previous=None
            def completed():
                nonlocal previous
                status=db.query_one("SELECT status FROM task WHERE task_no=:n",{"n":no})["status"]
                if status!=previous:
                    print(json.dumps({"case":state,"status":status},ensure_ascii=False),flush=True)
                    previous=status
                if status in {"FAILED","BLOCKED","TIMEOUT"}:raise AssertionError(status)
                return status=="DONE"
            wait(completed,300)
            wait(lambda:len(db.query("SELECT te.id FROM task_event te JOIN task t ON te.task_id=t.id "
                "WHERE t.task_no=:n AND te.event='result_received'",{"n":no}))==3,20)
            from app.results_api import list_results
            results=list_results(task_no=no)
            case={"state":state,"task_no":no,"results":[]}
            for result in sorted(results,key=lambda r:r["point_seq"]):
                file=config.RESULTS_DIR/result["image_url"].split("/")[-1]
                assert result["equipment_id"] in {"ELEC-001","AIR-001","PIPE-001"}
                assert result["inspection"]["checks"]
                case["results"].append({"equipment_id":result["equipment_id"],"image":str(file),
                    "observation":result["observation_name"],"sha256":hashlib.sha256(file.read_bytes()).hexdigest()})
            if args.vlm:
                wait(lambda:len(db.query("SELECT id FROM inspection_result WHERE task_no=:n",{"n":no}))==3,240)
                assessed=list_results(task_no=no)
                for image in case["results"]:
                    result=next(r for r in assessed if r["equipment_id"]==image["equipment_id"])
                    image.update(normal=result["normal"],checks=result["checks"],error=result["error"])
                    assert not result["error"],result
            report["cases"].append(case)
            (ROOT/".tmp/equipment-acceptance.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        for ident in ("ELEC-001","AIR-001","PIPE-001"):
            samples=[next(r for r in case["results"] if r["equipment_id"]==ident) for case in report["cases"]]
            assert samples[0]["sha256"]!=samples[1]["sha256"],ident+" image did not change"
        report["images_changed_for_all_devices"]=True
        if args.vlm:
            report["normal_case_passed"]=all(r["normal"]==1 for r in report["cases"][0]["results"])
            report["abnormal_case_detected"]=all(r["normal"]==0 for r in report["cases"][1]["results"])
        (ROOT/".tmp/equipment-acceptance.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        if args.vlm:
            assert report["normal_case_passed"],"正常场景存在误报或无法判断"
            assert report["abnormal_case_detected"],"异常场景存在漏检或无法判断"
        print("RESULT "+json.dumps(report,ensure_ascii=False),flush=True)
    finally:
        try:switch("normal")
        except Exception:pass
        if consumer:consumer.stop()
        workers.close()
        delivery.set_publisher(None)
        reliability.ReliabilityTests.tearDownClass()

if __name__=="__main__":main()
