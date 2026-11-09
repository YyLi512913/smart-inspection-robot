"""Integration tests against an isolated MySQL database; no production data mutations."""
import json
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock
from uuid import uuid4

from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import db, delivery, state_machine as sm
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "robot_ws" / "src" / "inspection_robot"))
from inspection_robot.durable_store import DurableStore


class ReliabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_engine = db.engine
        cls.database = "robot_inspection_test_" + uuid4().hex[:12]
        cls.admin = create_engine(db.engine.url.set(database="mysql"))
        with cls.admin.begin() as conn:
            conn.execute(text(f"CREATE DATABASE {cls.database} CHARACTER SET utf8mb4"))
        db.engine = create_engine(cls.admin.url.set(database=cls.database), pool_size=10)
        schema = (Path(__file__).resolve().parents[1] / "schema.sql").read_text(encoding="utf-8")
        schema = "\n".join(line for line in schema.splitlines() if not line.strip().startswith("--"))
        with db.engine.begin() as conn:
            for statement in schema.split(";"):
                if statement.strip() and not statement.strip().upper().startswith(("CREATE DATABASE", "USE ")):
                    conn.execute(text(statement))

    @classmethod
    def tearDownClass(cls):
        db.engine.dispose()
        db.engine = cls.original_engine
        with cls.admin.begin() as conn:
            conn.execute(text(f"DROP DATABASE {cls.database}"))
        cls.admin.dispose()

    def setUp(self):
        with db.engine.begin() as conn:
            for table in ("message_inbox", "mqtt_command", "task_request", "robot_execution",
                          "inspection_result", "task_event", "task_point", "task"):
                conn.execute(text(f"DELETE FROM {table}"))
        self.sent = []
        delivery.set_publisher(lambda topic, payload: self.sent.append((topic, payload)))
        self.points = [{"place": "power_room", "x": 14.75, "y": -11.0, "check_item": "外观"}]
        self.verdict = {"verdict": "feasible", "battery": 90}
        self.counter = 0

    def create(self, robot="robot1", key=None, points=None, verdict=None):
        return sm.create_task(robot, "manual", 5, "test", points or self.points,
                              verdict or self.verdict, request_key=key)

    def command(self, task_no, action="patrol"):
        return db.query_one("SELECT * FROM mqtt_command WHERE task_no=:n AND action=:a "
                            "ORDER BY created_at DESC", {"n": task_no, "a": action})

    def event(self, task_no, event, seq=None, command=None, event_id=None, robot="robot1"):
        cmd = command or self.command(task_no)
        payload = {"event_id": event_id or uuid4().hex, "command_id": cmd["command_id"],
                   "task_id": task_no, "event": event, "point_seq": seq}
        sm.handle_event(robot, payload)
        return payload

    def status(self, no):
        return db.query_one("SELECT status FROM task WHERE task_no=:n", {"n": no})["status"]

    def test_commit_before_publish_and_same_id_retry(self):
        no = self.create()
        self.assertEqual(self.sent, [])
        delivery.flush_pending()
        self.assertEqual(len(self.sent), 1)
        cmd_id = self.sent[0][1]["command_id"]
        db.execute("UPDATE mqtt_command SET last_sent_at=NULL")
        delivery.flush_pending()
        self.assertEqual([p["command_id"] for _, p in self.sent], [cmd_id, cmd_id])
        self.event(no, "accepted")
        db.execute("UPDATE mqtt_command SET last_sent_at=NULL")
        delivery.flush_pending()
        self.assertEqual(len(self.sent), 2)

    def test_publish_failure_stays_pending(self):
        no = self.create()
        delivery.set_publisher(Mock(side_effect=ConnectionError("offline")))
        with self.assertLogs("app.delivery", level="ERROR"):
            delivery.flush_pending()
        self.assertEqual(self.command(no)["status"], "pending")
        self.assertEqual(self.command(no)["attempts"], 0)

    def test_rollback_leaves_no_command_or_ownership(self):
        with self.assertRaises(KeyError):
            self.create(points=[{"place": "bad", "x": 1}])
        self.assertEqual(db.query("SELECT * FROM task"), [])
        self.assertEqual(db.query("SELECT * FROM mqtt_command"), [])
        self.assertEqual(db.query("SELECT * FROM robot_execution"), [])

    def test_concurrent_create_only_one_wins(self):
        def create():
            try:
                return self.create()
            except sm.TaskConflict:
                return "busy"
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(lambda _: create(), range(2)))
        self.assertEqual(result.count("busy"), 1)
        self.assertEqual(len(db.query("SELECT * FROM task")), 1)
        self.assertEqual(len(db.query("SELECT * FROM mqtt_command")), 1)

    def test_idempotent_creation_and_parameter_conflict(self):
        first = self.create(key="request-1")
        self.assertEqual(self.create(key="request-1"), first)
        with self.assertRaises(sm.TaskConflict):
            self.create(key="request-1", points=[{**self.points[0], "check_item": "不同请求"}])
        self.assertEqual(len(db.query("SELECT * FROM task")), 1)

    def test_same_key_concurrent_requests(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(lambda _: self.create(key="same-request"), range(2)))
        self.assertEqual(result[0], result[1])

    def test_unknown_robot_and_illegal_transition_cannot_mutate(self):
        no = self.create()
        self.event(no, "accepted", robot="robot2")
        self.assertEqual(self.status(no), "DISPATCHED")
        self.event(no, "task_done")
        self.assertEqual(self.status(no), "DISPATCHED")
        self.event(no, "point_arrived", 1)
        self.assertEqual(self.status(no), "DISPATCHED")
        self.event(no, "accepted")
        self.event(no, "task_done")
        self.assertEqual(self.status(no), "ACCEPTED")

    def test_duplicate_event_has_one_audit_and_no_regression(self):
        no = self.create()
        payload = self.event(no, "accepted")
        self.event(no, "navigating", 1)
        sm.handle_event("robot1", payload)
        self.assertEqual(self.status(no), "NAVIGATING")
        rows = db.query("SELECT id FROM task_event WHERE event='accepted'")
        self.assertEqual(len(rows), 1)

    def test_wrong_point_order_rejected(self):
        no = self.create(points=self.points + [{**self.points[0], "place": "server_room", "x": 21.75}])
        self.event(no, "accepted")
        self.event(no, "point_arrived", 2)
        self.assertEqual(self.status(no), "ACCEPTED")
        self.event(no, "navigating", 1)
        self.event(no, "point_arrived", 1)
        self.assertEqual(self.status(no), "INSPECTING")

    def test_cancel_holds_slot_until_matching_ack(self):
        no = self.create()
        old = self.command(no)
        sm.cancel(no)
        sm.cancel(no)
        self.assertEqual(len(db.query("SELECT * FROM mqtt_command WHERE action='cancel'")), 1)
        with self.assertRaises(sm.TaskConflict):
            self.create()
        self.event(no, "cancel_confirmed", command=old)
        with self.assertRaises(sm.TaskConflict):
            self.create()
        self.event(no, "cancel_confirmed", command=self.command(no, "cancel"))
        new = self.create()
        self.assertNotEqual(no, new)

    def test_retry_new_generation_rejects_old_events(self):
        no = self.create()
        old = self.command(no)
        self.event(no, "accepted")
        self.event(no, "navigating", 1)
        self.event(no, "blocked", 1)
        self.assertEqual(sm.decision(no, "retry"), "DISPATCHED")
        self.event(no, "accepted")
        self.event(no, "navigating", 1)
        self.event(no, "blocked", 1, command=old)
        self.assertEqual(self.status(no), "NAVIGATING")
        self.assertNotEqual(self.command(no)["command_id"], old["command_id"])

    def test_last_point_skip_completes_and_releases(self):
        no = self.create()
        self.event(no, "accepted")
        self.event(no, "blocked", 1)
        self.assertEqual(sm.decision(no, "skip"), "DONE")
        self.create()

    def test_late_result_dedup_and_done_not_reopened(self):
        no = self.create()
        self.event(no, "accepted")
        self.event(no, "navigating", 1)
        self.event(no, "point_arrived", 1)
        self.event(no, "task_done")
        payload = {"task_id": no, "command_id": self.command(no)["command_id"],
                   "event_id": uuid4().hex, "point_seq": 1, "place": "power_room", "image_url": None}
        sm.handle_result("robot1", payload)
        sm.handle_result("robot1", payload)
        sm.handle_result("robot1", {**payload, "event_id": uuid4().hex})
        self.assertEqual(self.status(no), "DONE")
        self.assertEqual(len(db.query("SELECT id FROM task_event WHERE event='result_received'")), 1)

    def test_timeout_cancels_and_holds_slot(self):
        no = self.create()
        db.execute("UPDATE config SET cfg_value='1' WHERE cfg_key='watchdog_timeout_s'")
        db.execute("UPDATE task_event SET created_at=NOW(3)-INTERVAL 5 SECOND")
        self.assertEqual(sm.check_timeouts(), [no])
        self.assertEqual(self.status(no), "TIMEOUT")
        self.assertEqual(self.command(no, "cancel")["status"], "pending")
        with self.assertRaises(sm.TaskConflict):
            self.create()
        self.event(no, "cancel_confirmed", command=self.command(no, "cancel"))
        self.create()
        db.execute("UPDATE config SET cfg_value='300' WHERE cfg_key='watchdog_timeout_s'")

    def test_deferred_reassesses_energy(self):
        no = self.create(verdict={"verdict": "can_reach_dock"})
        db.execute("UPDATE config SET cfg_value='100' WHERE cfg_key='energy_per_point_pct'")
        sm.try_resume_deferred("robot1", 90, {"x": 2, "y": -3})
        self.assertEqual(self.status(no), "DEFERRED")
        db.execute("UPDATE config SET cfg_value='2' WHERE cfg_key='energy_per_point_pct'")
        sm.try_resume_deferred("robot1", 90, {"x": 2, "y": -3})
        self.assertEqual(self.status(no), "DISPATCHED")

    def test_delivery_restart_recovers_committed_rows(self):
        no = self.create()
        delivery.set_publisher(None)
        delivery.flush_pending()
        self.assertEqual(self.sent, [])
        delivery.set_publisher(lambda topic, payload: self.sent.append((topic, payload)))
        delivery.flush_pending()
        self.assertEqual(self.sent[0][1]["task_id"], no)


    def test_out_of_generation_photo_is_not_recorded(self):
        no = self.create()
        old = self.command(no)
        self.event(no, "accepted")
        self.event(no, "blocked", 1)
        sm.decision(no, "retry")
        self.event(no, "accepted")
        self.event(no, "navigating", 1)
        self.event(no, "point_arrived", 1)
        sm.handle_result("robot1", {"task_id": no, "command_id": old["command_id"],
            "event_id": uuid4().hex, "point_seq": 1, "place": "power_room", "image_url": None})
        self.assertEqual(db.query("SELECT id FROM task_event WHERE event='result_received'"), [])

    def test_recovery_blocks_retry_without_reconciliation(self):
        no = self.create()
        self.event(no, "accepted")
        self.event(no, "recovery_required")
        self.assertEqual(self.status(no), "BLOCKED")
        with self.assertRaises(sm.TaskConflict):
            sm.decision(no, "retry")
        with self.assertRaises(sm.TaskConflict):
            self.create()

    def test_invalid_public_point_is_rejected(self):
        from pydantic import ValidationError
        from app.tasks_api import PointIn
        with self.assertRaises(ValidationError):
            PointIn(place="power_room", x=0, y=0)
        with self.assertRaises(ValidationError):
            PointIn(place="power_room", x=float("nan"), y=-11)
        PointIn(place="power_room", x=14.75, y=-11)


class RobotJournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "journal.sqlite3")
        self.store = DurableStore(self.path)
        self.command = {"command_id": "cmd1", "task_id": "T1", "action": "patrol", "points": [{"seq": 1}]}

    def tearDown(self):
        self.tmp.cleanup()

    def test_duplicate_and_busy_never_replace_active(self):
        self.assertEqual(self.store.begin(self.command), "new")
        self.assertEqual(self.store.begin(self.command), "duplicate")
        self.assertEqual(self.store.begin({**self.command, "command_id": "cmd2", "task_id": "T2"}), "busy")
        self.assertEqual(self.store.active()["task_id"], "T1")
        self.assertEqual(self.store.begin({**self.command, "points": []}), "conflict")

    def test_disk_restart_recovers_active_and_outgoing(self):
        self.store.begin(self.command)
        self.store.save_runtime("cmd1", {"task_id": "T1", "phase": "submitting"})
        emitted = self.store.emit({"command_id": "cmd1", "task_id": "T1", "event": "accepted"})
        restarted = DurableStore(self.path)
        self.assertEqual(json.loads(restarted.active()["runtime"])["phase"], "submitting")
        self.assertEqual(restarted.next_message()["event_id"], emitted["event_id"])

    def test_spool_order_ack_and_replay(self):
        self.store.begin(self.command)
        accepted = self.store.emit({"command_id": "cmd1", "task_id": "T1", "event": "accepted"})
        self.store.enqueue("result", {"event_id": "result1"})
        self.assertEqual(self.store.next_message()["event_id"], accepted["event_id"])
        self.store.acknowledge(accepted["event_id"])
        self.assertEqual(self.store.next_message()["event_id"], "result1")
        self.store.acknowledge("result1")
        self.store.replay("cmd1")
        self.assertEqual(self.store.next_message()["event_id"], accepted["event_id"])

    def test_cancel_tombstone_blocks_delayed_patrol_across_restart(self):
        self.store.cancel({"task_id": "T1"})
        restarted = DurableStore(self.path)
        self.assertEqual(restarted.begin(self.command), "canceled")

    def test_terminal_duplicate_cannot_execute_again(self):
        self.store.begin(self.command)
        self.store.emit({"command_id": "cmd1", "task_id": "T1", "event": "task_done"}, state="done")
        self.assertIsNone(self.store.active())
        self.assertEqual(self.store.begin(self.command), "duplicate")
        self.assertEqual(self.store.begin({**self.command, "command_id": "cmd2", "task_id": "T2"}), "new")


    def test_rejected_command_cannot_start_when_robot_later_becomes_free(self):
        self.store.begin(self.command)
        rejected = {**self.command, "command_id": "cmd2", "task_id": "T2"}
        self.assertEqual(self.store.begin(rejected), "busy")
        self.store.emit({"command_id": "cmd2", "task_id": "T2", "event": "command_rejected"}, state="rejected")
        self.store.emit({"command_id": "cmd1", "task_id": "T1", "event": "task_done"}, state="done")
        self.assertEqual(self.store.begin(rejected), "duplicate")
        self.assertIsNone(self.store.active())

    def test_snapshot_request_survives_restart_and_is_completed_atomically(self):
        self.store.begin(self.command)
        request = {"task_id": "T1", "command_id": "cmd1", "point_seq": 1}
        arrived = self.store.emit({"command_id": "cmd1", "task_id": "T1", "event": "point_arrived"},
                                 snapshot=request)
        restarted = DurableStore(self.path)
        self.assertEqual(restarted.next_snapshot(), request)
        restarted.finish_snapshot(request, {"event_id": "image1", **request})
        restarted.request_snapshot(request) # duplicate ROS request cannot reopen it
        self.assertIsNone(restarted.next_snapshot())
        self.assertEqual(restarted.next_message()["event_id"], arrived["event_id"])
        restarted.acknowledge(arrived["event_id"])
        self.assertEqual(restarted.next_message()["event_id"], "image1")


class ConsumerAckTests(unittest.TestCase):
    def test_no_application_ack_before_database_success(self):
        from app.mqtt_consumer import MqttConsumer
        from unittest.mock import patch
        consumer = MqttConsumer()
        consumer.publish = Mock()
        message = Mock(topic="robot/robot1/event", payload=json.dumps({
            "event_id": "e1", "command_id": "c1", "task_id": "t1", "event": "accepted"}).encode())
        with patch("app.mqtt_consumer.state_machine.handle_event", side_effect=RuntimeError("db unavailable")):
            with self.assertLogs("mqtt_consumer", level="ERROR"):
                consumer._on_message(None, None, message)
        consumer.publish.assert_not_called()
        with patch("app.mqtt_consumer.state_machine.handle_event"):
            consumer._on_message(None, None, message)
        consumer.publish.assert_called_once_with("robot/robot1/ack", {"event_id": "e1"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
