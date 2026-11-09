"""ROS-node tests with controlled action responses; no simulation or robot movement."""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import rclpy
from action_msgs.msg import GoalStatus
from rclpy.task import Future
from sensor_msgs.msg import Image

from inspection_robot.nav2_task_runner import Nav2TaskRunner
from inspection_robot.snapshot_node import SnapshotNode


class RosTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        rclpy.init(args=["--ros-args","-p","journal_path:="+self.temp.name+"/journal.sqlite3",
                        "-p","output_dir:="+self.temp.name+"/images"])
        self.nodes=[]

    def tearDown(self):
        for node in self.nodes:node.destroy_node()
        rclpy.shutdown()
        self.temp.cleanup()

    def runner(self):
        node=Nav2TaskRunner()
        self.nodes.append(node)
        node._stopped=Mock(return_value=True)
        return node

    def begin(self,node):
        payload={"protocol_version":2,"command_id":uuid4().hex,"task_id":"T-test","action":"patrol",
                 "points":[{"seq":1,"place":"corridor_west","x":5.75,"y":-3.0}]}
        node._start_task(payload)
        response=Future()
        result=Future()
        node.nav=Mock()
        node.nav.send_goal_async.return_value=response
        node._submit_goal()
        handle=Mock(accepted=True)
        handle.get_result_async.return_value=result
        response.set_result(handle)
        return payload,result


class Nav2ActionTests(RosTest):
    def test_second_executor_cannot_claim_same_robot_journal(self):
        node=self.runner()
        with self.assertRaises(RuntimeError):
            Nav2TaskRunner()

    def test_restart_queries_persisted_uuid_and_unknown_does_not_release(self):
        original=self.runner()
        payload,result=self.begin(original)
        goal_id=original.task["dispatch_id"]
        original.destroy_node()
        self.nodes.remove(original)
        node=self.runner()
        self.assertEqual(node.task["phase"],"recovery")
        self.assertEqual(node.task["dispatch_id"],goal_id)
        node.cancel_client=Mock()
        node.result_client=Mock()
        reply=Future()
        node.result_client.call_async.return_value=reply
        node._cancel_task({"task_id":payload["task_id"],"command_id":"cancel-restarted","action":"cancel"})
        reply.set_result(SimpleNamespace(status=GoalStatus.STATUS_UNKNOWN))
        node._tick()
        self.assertIsNotNone(node.task)
        self.assertIsNotNone(node.store.active())
        query=node.result_client.call_async.call_args.args[0]
        self.assertEqual(bytes(query.goal_id.uuid).hex(),goal_id)
        terminal=Future()
        terminal.set_result(SimpleNamespace(status=GoalStatus.STATUS_CANCELED))
        node._stop_result(terminal,(node.task["command_id"],node.task["idx"]))
        node._tick()
        self.assertIsNone(node.task)
        self.assertIsNone(node.store.active())

    def test_success_waits_for_stopped_and_camera_result(self):
        node=self.runner()
        payload,result=self.begin(node)
        result.set_result(SimpleNamespace(status=GoalStatus.STATUS_SUCCEEDED))
        node._stopped.return_value=False
        node._tick()
        self.assertEqual(node.task["phase"],"settling")
        self.assertIsNone(node.store.next_snapshot())
        node._stopped.return_value=True
        node._tick()
        self.assertEqual(node.task["phase"],"inspecting")
        node._tick()
        self.assertIsNotNone(node.task)
        request=node.store.next_snapshot()
        node.store.finish_snapshot(request,{"event_id":"result1","image_url":"/api/results/img/test.jpg"})
        node._tick()
        self.assertIsNone(node.task)
        self.assertEqual(node.store.command(payload["command_id"])["state"],"done")

    def test_cancel_acceptance_does_not_release_before_terminal_result(self):
        node=self.runner()
        payload,result=self.begin(node)
        node.cancel_client=Mock()
        node.result_client=Mock()
        node.result_client.service_is_ready.return_value=False
        node._cancel_task({"task_id":payload["task_id"],"command_id":"cancel1","action":"cancel"})
        self.assertIsNotNone(node.task)
        self.assertIsNotNone(node.store.active())
        result.set_result(SimpleNamespace(status=GoalStatus.STATUS_CANCELED))
        node._stopped.return_value=False
        node._tick()
        self.assertIsNotNone(node.task)
        node._stopped.return_value=True
        node._tick()
        self.assertIsNone(node.task)
        self.assertIsNone(node.store.active())
        args=node.cancel_client.call_async.call_args.args[0]
        self.assertEqual(bytes(args.goal_info.goal_id.uuid).hex(),
                         json.loads(node.store.command(payload["command_id"])["runtime"])["dispatch_id"])

    def test_aborted_action_cannot_report_arrival(self):
        node=self.runner()
        payload,result=self.begin(node)
        result.set_result(SimpleNamespace(status=GoalStatus.STATUS_ABORTED))
        self.assertIsNone(node.task)
        self.assertIsNone(node.store.next_snapshot())
        self.assertEqual(node.store.command(payload["command_id"])["state"],"blocked")

    def test_camera_failure_fails_task_without_reporting_success(self):
        node=self.runner()
        payload,result=self.begin(node)
        result.set_result(SimpleNamespace(status=GoalStatus.STATUS_SUCCEEDED))
        node._tick()
        request=node.store.next_snapshot()
        node.store.finish_snapshot(request,{"event_id":"result1","image_url":None,"capture_error":"camera timeout"})
        node._tick()
        cmd=node.store.command(payload["command_id"])
        self.assertEqual(cmd["state"],"failed")
        self.assertEqual(json.loads(cmd["last_event"])["event"],"task_failed")


class CameraTests(RosTest):
    def node(self):
        node=SnapshotNode()
        self.nodes.append(node)
        return node

    def request(self,node):
        req={"command_id":"capture1","task_id":"T-capture","point_seq":1,"place":"workshop","min_stamp_ns":10_000_000_000}
        node.store.request_snapshot(req)
        return req

    def frame(self,node,seconds):
        frame=Image()
        frame.header.stamp.sec=seconds
        frame.height=4
        frame.width=4
        frame.encoding="rgb8"
        frame.step=12
        frame.data=bytes([220,90,40])*16
        node._image_cb(frame)

    def test_old_frame_is_not_used_and_timeout_is_explicit(self):
        node=self.node()
        req=self.request(node)
        self.frame(node,9)
        node._process_request()
        self.assertIsNone(node.store.snapshot_result("capture1",1))
        node.pending_since=time.monotonic()-node.timeout-1
        node._process_request()
        result=node.store.snapshot_result("capture1",1)
        self.assertIsNone(result["image_url"])
        self.assertTrue(result["capture_error"])
        self.assertEqual(list((Path(self.temp.name)/"images").glob("*.jpg")),[])

    def test_new_frame_is_saved_and_provenance_is_recorded(self):
        node=self.node()
        self.request(node)
        self.frame(node,10)
        node._process_request()
        result=node.store.snapshot_result("capture1",1)
        self.assertEqual(result["image_source"],"camera")
        self.assertEqual(result["camera_stamp_ns"],10_000_000_000)
        self.assertEqual(result["capture_error"],"")
        path=Path(self.temp.name)/"images"/result["image_url"].split("/")[-1]
        self.assertGreater(path.stat().st_size,100)
        self.assertEqual(list(path.parent.glob("*.tmp")),[])


class MapConnectivityTests(unittest.TestCase):
    def test_all_inspection_points_are_reachable_with_robot_clearance(self):
        from collections import deque
        import cv2
        from inspection_robot.task_runner import PLACES
        map_path=Path(__file__).resolve().parents[1]/"maps/factory.pgm"
        occupied=cv2.imread(str(map_path),cv2.IMREAD_GRAYSCALE)
        self.assertIsNotNone(occupied)
        free=cv2.erode((occupied>200).astype("uint8"),cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(11,11)))
        height,width=free.shape
        def cell(x,y):return int((x+1)/.05),height-1-int((y+17)/.05)
        start=cell(*PLACES["tinyRobot1_charger"])
        queue=deque([start]);seen={start}
        while queue:
            x,y=queue.popleft()
            for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)):
                p=(x+dx,y+dy)
                if 0<=p[0]<width and 0<=p[1]<height and free[p[1],p[0]] and p not in seen:
                    seen.add(p);queue.append(p)
        from inspection_robot.observation_catalog import load_observations
        targets={**PLACES,**{k:(p["x"],p["y"]) for k,p in load_observations().items()}}
        for name,xy in targets.items():
            self.assertIn(cell(*xy),seen,name+" is unreachable for the robot footprint")


if __name__=="__main__":
    unittest.main(verbosity=2)
