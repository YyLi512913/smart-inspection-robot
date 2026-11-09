import json
import unittest
from uuid import uuid4

from app import db, equipment, state_machine as sm, vlm
import test_reliability as reliability


class EquipmentTests(unittest.TestCase):
    setUpClass=classmethod(reliability.ReliabilityTests.setUpClass.__func__)
    tearDownClass=classmethod(reliability.ReliabilityTests.tearDownClass.__func__)
    create=reliability.ReliabilityTests.create
    command=reliability.ReliabilityTests.command
    event=reliability.ReliabilityTests.event
    status=reliability.ReliabilityTests.status

    def setUp(self):
        reliability.ReliabilityTests.setUp(self)
        with db.transaction() as conn:
            for table in ("observation_check","observation_point","equipment"):
                conn.execute(db.text("DELETE FROM "+table))
        equipment.seed_catalog()

    def test_workshop_expands_to_three_equipment_observations(self):
        points=equipment.expand_targets([{"place":"workshop"}])
        self.assertEqual(len(points),3)
        self.assertEqual({p["inspection"]["equipment_id"] for p in points},{"ELEC-001","AIR-001","PIPE-001"})
        self.assertEqual(len({p["place"] for p in points}),3)
        self.assertTrue(all(p["inspection"]["checks"] for p in points))
        self.assertTrue(all("yaw" in p["inspection"] for p in points))

    def test_device_and_observation_selection_do_not_expand_entire_area(self):
        point=equipment.expand_targets([{"equipment_id":"AIR-001"}])[0]
        self.assertEqual(point["place"],"obs_air001_gauge")
        self.assertEqual(point["inspection"]["checks"][0]["max"],.8)
        self.assertEqual(equipment.expand_targets([{"observation_id":point["place"]}]),[point])

    def test_overlapping_targets_are_deduplicated(self):
        self.assertEqual(len(equipment.expand_targets([{"area":"workshop"},{"equipment_id":"AIR-001"}])),3)

    def test_unknown_ambiguous_and_disabled_targets_are_rejected(self):
        for target in ({"equipment_id":"made-up"},{"area":"workshop","equipment_id":"AIR-001"}):
            with self.assertRaises(ValueError):equipment.expand_targets([target])
        db.execute("UPDATE equipment SET enabled=0 WHERE equipment_id='AIR-001'")
        with self.assertRaises(ValueError):equipment.expand_targets([{"equipment_id":"AIR-001"}])

    def test_task_rule_snapshot_survives_catalog_changes_and_retry(self):
        point=equipment.expand_targets([{"equipment_id":"AIR-001"}])[0]
        no=self.create(points=[point])
        self.event(no,"accepted")
        self.event(no,"blocked",1)
        db.execute("UPDATE equipment SET name='修改后的名称' WHERE equipment_id='AIR-001'")
        db.execute("UPDATE observation_check SET definition=:d WHERE observation_id='obs_air001_gauge'",
                   {"d":json.dumps({**point["inspection"]["checks"][0],"max":10})})
        sm.decision(no,"retry")
        payload=json.loads(self.command(no)["payload"])
        snapshot=payload["points"][0]["inspection"]
        self.assertEqual(snapshot["equipment_name"],"1号空压机压力表")
        self.assertEqual(snapshot["checks"][0]["max"],.8)

    def test_result_uses_authoritative_task_snapshot_not_robot_claim(self):
        no=self.create(points=equipment.expand_targets([{"equipment_id":"ELEC-001"}]))
        self.event(no,"accepted")
        self.event(no,"navigating",1)
        self.event(no,"point_arrived",1)
        sm.handle_result("robot1",{"event_id":uuid4().hex,"command_id":self.command(no)["command_id"],
            "task_id":no,"point_seq":1,"place":"obs_elec001_front","image_url":None,
            "inspection":{"equipment_id":"FORGED","equipment_name":"伪造"}})
        result=db.query_one("SELECT detail FROM task_event WHERE event='result_received'")
        self.assertEqual(json.loads(result["detail"])["inspection"]["equipment_id"],"ELEC-001")
        from app.results_api import list_results
        self.assertEqual(list_results(equipment_id="ELEC-001")[0]["equipment_name"],"1号控制柜")
        self.assertEqual(list_results(equipment_id="AIR-001"),[])

    def test_request_identity_replays_original_after_equipment_disabled(self):
        identity={"targets":[{"equipment_id":"AIR-001"}]}
        points=equipment.expand_targets(identity["targets"])
        no=sm.create_task("robot1","manual",5,"test",points,self.verdict,
                          request_key="asset-request",request_identity=identity)
        db.execute("UPDATE equipment SET enabled=0 WHERE equipment_id='AIR-001'")
        replay=sm.create_task("robot1","manual",5,"test",[],{},request_key="asset-request",request_identity=identity)
        self.assertEqual(replay,no)

    def test_expanded_points_used_in_energy_assessment(self):
        from app import energy_assessor
        points=equipment.expand_targets([{"area":"workshop"}])
        result=energy_assessor.assess(points,2,-3,100)
        self.assertGreaterEqual(result["need_pct"],3*2.0*1.2)

    def test_task_creation_requires_one_route_source(self):
        from app.tasks_api import TaskCreate
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            TaskCreate(robot_id="robot1",points=self.points,targets=[{"area":"workshop"}])
        self.assertEqual(TaskCreate(robot_id="robot1",targets=[{"equipment_id":"AIR-001"}]).targets[0]["equipment_id"],"AIR-001")


class AssessmentTests(unittest.TestCase):
    rule={"code":"pressure","name":"压力","kind":"numeric","min":.2,"max":.8,"unit":"MPa"}

    def parse_value(self,value,unit="MPa"):
        return vlm._parse(json.dumps({"normal":True,"checks":[{"code":"pressure","status":"normal",
                        "value":value,"unit":unit,"evidence":"数字读数"}]}),[self.rule])

    def test_numeric_limits_override_model_claim_of_normal(self):
        self.assertFalse(self.parse_value(1.2)["normal"])
        self.assertTrue(self.parse_value(.45)["normal"])
        self.assertTrue(self.parse_value(.2)["normal"])
        self.assertTrue(self.parse_value(.8)["normal"])

    def test_unreadable_or_wrong_unit_never_becomes_normal(self):
        for value,unit in ((None,"MPa"),("0.45","MPa"),(.45,"bar"),(True,"MPa")):
            self.assertIsNone(self.parse_value(value,unit)["normal"])

    def test_missing_check_requires_review(self):
        output=vlm._parse('{"normal":true,"checks":[]}',[self.rule])
        self.assertIsNone(output["normal"])
        self.assertEqual(output["checks"][0]["status"],"unreadable")

    def test_string_boolean_is_rejected(self):
        with self.assertRaises(ValueError):vlm._parse('{"normal":"false"}')
