"""Nav2 action adapter. Navigation success, stop confirmation, and fresh camera capture."""
import json
import math
import time
from uuid import NAMESPACE_URL, uuid5

import rclpy
from action_msgs.msg import GoalStatus
from action_msgs.srv import CancelGoal
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose
from nav_msgs.msg import Odometry
from rclpy.action import ActionClient
from rclpy.node import Node
from std_msgs.msg import String
from unique_identifier_msgs.msg import UUID

from .durable_store import DurableStore, default_path
from .task_runner import TaskRunner, PLACES
from .observation_catalog import load_observations

TERMINAL = {GoalStatus.STATUS_SUCCEEDED, GoalStatus.STATUS_CANCELED, GoalStatus.STATUS_ABORTED}
# Face the workbench/cabinets/racks from the named inspection pose.
YAW = {"workshop":-.55,"power_room":-2.034,"server_room":-1.487}


class Nav2TaskRunner(TaskRunner):
    def __init__(self):
        Node.__init__(self, "task_runner")
        for key,value in [("robot_id","robot1"),("journal_path",""),
                          ("nav_timeout_s",240.0),("startup_timeout_s",45.0),
                          ("action_name","/navigate_to_pose"),("global_frame","map")]:
            self.declare_parameter(key,value)
        self.adapter_name="nav2"
        self.observations=load_observations()
        self.point_catalog={**PLACES,**{k:(v["x"],v["y"]) for k,v in self.observations.items()}}
        self.nav_timeout=float(self.get_parameter("nav_timeout_s").value)
        self.startup_timeout=float(self.get_parameter("startup_timeout_s").value)
        self.action_name=self.get_parameter("action_name").value
        self.global_frame=self.get_parameter("global_frame").value
        path=self.get_parameter("journal_path").value or default_path(self.get_parameter("robot_id").value)
        self.store=DurableStore(path)
        try:
            self.execution_lock = self.store.acquire_execution_lock()
        except Exception:
            Node.destroy_node(self)
            raise
        self.task=None
        self.stop_request=None
        self.stop_future=None
        self.stop_sent_at=0
        self.goal_handle=None
        self.goal_terminal=None
        self.stop_query_at=0
        self.odom_at=0
        self.speed=float("inf")
        self.event_pub=self.create_publisher(String,"/inspection/event",10)
        self.snapshot_pub=self.create_publisher(String,"/inspection/snapshot_request",10)
        self.current_pub=self.create_publisher(String,"/inspection/current_task",10)
        self.create_subscription(String,"/inspection/task",self._task_cb,10)
        self.create_subscription(Odometry,"/odom",self._odom_cb,10)
        self.nav=ActionClient(self,NavigateToPose,self.action_name)
        self.cancel_client=self.create_client(CancelGoal,self.action_name+"/_action/cancel_goal")
        self.result_client=self.create_client(NavigateToPose.Impl.GetResultService,self.action_name+"/_action/get_result")
        active=self.store.active()
        if active:
            self.task=json.loads(active["runtime"]) if active["runtime"] else {
                "task_id":active["task_id"],"command_id":active["command_id"],
                "points":json.loads(active["payload"])["points"],"idx":0,"phase":"idle","dispatch_id":None}
            previous=self.task["phase"]
            self.task["phase"]="recovery"
            self.task["recovery_phase"]=previous
            self._emit("recovery_required",detail={"reason":"executor_restart","previous_phase":previous,"adapter":"nav2"})
        self.create_timer(.1,self._tick)
        self._publish_current()

    def _odom_cb(self,msg):
        twist=msg.twist.twist
        self.speed=max(math.hypot(twist.linear.x,twist.linear.y),abs(twist.angular.z))
        self.odom_at=time.monotonic()

    def _stopped(self):
        return time.monotonic()-self.odom_at<1.5 and self.speed<.025

    def _send_current_point(self):
        self.goal_handle=None
        self.goal_terminal=None
        self.task["dispatch_id"]=None
        self.task["phase"]="waiting_nav2"
        self.task["sent_at"]=time.monotonic()
        self._save()
        self._publish_current()

    def _submit_goal(self):
        p=self.task["points"][self.task["idx"]]
        uuid=uuid5(NAMESPACE_URL,f"{self.task['command_id']}:nav2:{p['seq']}")
        self.task["dispatch_id"]=uuid.hex
        self.task["phase"]="submitting"
        self.task["sent_at"]=time.monotonic()
        self._save()
        self._emit("navigating",p["seq"])
        goal=NavigateToPose.Goal()
        goal.pose=PoseStamped()
        goal.pose.header.frame_id=self.global_frame
        goal.pose.header.stamp=self.get_clock().now().to_msg()
        goal.pose.pose.position.x=float(p["x"])
        goal.pose.pose.position.y=float(p["y"])
        yaw=float(p.get("yaw",YAW.get(p["place"],0.0)))
        goal.pose.pose.orientation.z=math.sin(yaw/2)
        goal.pose.pose.orientation.w=math.cos(yaw/2)
        token=(self.task["command_id"],self.task["idx"])
        self.nav.send_goal_async(goal,goal_uuid=UUID(uuid=list(uuid.bytes))).add_done_callback(
            lambda future:self._goal_response(future,token))

    def _matches(self,token):
        return self.task and (self.task["command_id"],self.task["idx"])==token

    def _goal_response(self,future,token):
        if not self._matches(token):
            return
        try:
            handle=future.result()
        except Exception as exc:
            self.task["phase"]="recovery"
            self._save()
            self._emit("recovery_required",detail={"reason":str(exc),"goal_id":self.task["dispatch_id"]})
            return
        if not handle.accepted:
            self.goal_terminal=GoalStatus.STATUS_ABORTED
            self.task["dispatch_id"]=None
            if self.stop_request:
                self._advance_stop()
            else:
                self._on_stuck(self.task["points"][self.task["idx"]],"Nav2 拒绝目标")
            return
        self.goal_handle=handle
        self.task["phase"]="navigating"
        self._save()
        handle.get_result_async().add_done_callback(lambda f:self._goal_result(f,token))

    def _goal_result(self,future,token):
        if not self._matches(token):
            return
        try:
            result=future.result()
            self.goal_terminal=result.status
        except Exception as exc:
            self._emit("recovery_required",detail={"reason":str(exc)})
            self.task["phase"]="recovery"
            self._save()
            return
        if self.stop_request:
            return
        if result.status==GoalStatus.STATUS_SUCCEEDED:
            self.task["phase"]="settling"
            self.task["sent_at"]=time.monotonic()
            self._save()
        else:
            self._on_stuck(self.task["points"][self.task["idx"]],
                           f"Nav2 执行未成功，状态 {result.status}")

    def _advance_stop(self):
        stop=self.stop_request
        if not stop:return
        matches=self.task and (stop["kind"]=="blocked" or self.task["task_id"]==stop["payload"]["task_id"])
        if not matches:
            self._finish_stop()
            return
        # Nothing was sent when waiting_nav2/idle; still require fresh stationary odometry.
        if not self.task.get("dispatch_id"):
            if self._stopped():self._finish_stop()
            return
        if self.goal_terminal in TERMINAL:
            if self._stopped():self._finish_stop()
            return
        if time.monotonic()-self.stop_query_at<2:return
        self.stop_query_at=time.monotonic()
        if not self.cancel_client.service_is_ready():return
        goal_id=UUID(uuid=list(bytes.fromhex(self.task["dispatch_id"])))
        request=CancelGoal.Request()
        request.goal_info.goal_id=goal_id
        self.cancel_client.call_async(request)
        # Recover terminal result by persisted UUID, including after executor restart.
        if self.result_client.service_is_ready():
            query=NavigateToPose.Impl.GetResultService.Request()
            query.goal_id=goal_id
            token=(self.task["command_id"],self.task["idx"])
            self.result_client.call_async(query).add_done_callback(lambda f:self._stop_result(f,token))
        # Unknown goal or lost response is not proof that motion has stopped.

    def _stop_result(self,future,token):
        if not self._matches(token):return
        try:
            status=future.result().status
            if status in TERMINAL:self.goal_terminal=status
        except Exception as exc:
            self.get_logger().warn(f"查询导航终态失败: {exc}")

    def _on_arrived(self,point):
        request={"task_id":self.task["task_id"],"command_id":self.task["command_id"],
                 "point_seq":point["seq"],"place":point["place"],"check":point.get("check",""),
                 "inspection":point.get("inspection"),
                 "requested_at":time.time(),"min_stamp_ns":self.get_clock().now().nanoseconds}
        self._emit("point_arrived",point["seq"],snapshot=request)
        self.snapshot_pub.publish(String(data=json.dumps(request,ensure_ascii=False)))
        self.task["phase"]="inspecting"
        self._save()

    def _tick(self):
        self._publish_current()
        if self.stop_request:
            self._advance_stop()
            return
        if not self.task:return
        phase=self.task["phase"]
        if phase=="waiting_nav2":
            if self.nav.server_is_ready():
                self._submit_goal()
            elif time.monotonic()-self.task["sent_at"]>self.startup_timeout:
                self._on_stuck(self.task["points"][self.task["idx"]],"Nav2 服务未就绪")
        elif phase in {"navigating","submitting"} and time.monotonic()-self.task["sent_at"]>self.nav_timeout:
            self._on_stuck(self.task["points"][self.task["idx"]],"Nav2 导航超时")
        elif phase=="settling":
            if self._stopped():
                self._on_arrived(self.task["points"][self.task["idx"]])
            elif time.monotonic()-self.task["sent_at"]>10:
                self._on_stuck(self.task["points"][self.task["idx"]],"到点后速度未归零")
        elif phase=="inspecting":
            point=self.task["points"][self.task["idx"]]
            result=self.store.snapshot_result(self.task["command_id"],point["seq"])
            if result is None:return
            if not result.get("image_url"):
                self._emit("task_failed",detail={"reason":result.get("capture_error","图像采集失败")},state="failed")
                self.task=None
                return
            self.task["idx"]+=1
            self.task["phase"]="idle"
            self.task["dispatch_id"]=None
            self._save()
            if self.task["idx"]>=len(self.task["points"]):
                self._emit("task_done",state="done")
                self.task=None
            else:self._send_current_point()


def main(args=None):
    rclpy.init(args=args)
    node=Nav2TaskRunner()
    try:rclpy.spin(node)
    except KeyboardInterrupt:pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
