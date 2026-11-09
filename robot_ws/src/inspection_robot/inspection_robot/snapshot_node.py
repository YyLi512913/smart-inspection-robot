"""Capture a fresh ROS camera frame at an inspection pose; no sample fallback."""
import json
import os
import re
import time
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import cv2
import rclpy
from cv_bridge import CvBridge
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image
from std_msgs.msg import String

from .durable_store import DurableStore, default_path


class SnapshotNode(Node):
    def __init__(self):
        super().__init__("snapshot_node")
        for key,value in [("robot_id","robot1"),("journal_path",""),
                          ("output_dir","/mnt/f/robot-inspection/backend/static/results"),
                          ("backend_base",""),("camera_topic","/camera/image_raw"),
                          ("camera_timeout_s",8.0),("frame_max_age_s",2.0)]:
            self.declare_parameter(key,value)
        self.store=DurableStore(self.get_parameter("journal_path").value or default_path(self.get_parameter("robot_id").value))
        self.out_dir=Path(self.get_parameter("output_dir").value)
        self.out_dir.mkdir(parents=True,exist_ok=True)
        self.base=self.get_parameter("backend_base").value.rstrip("/")
        self.timeout=float(self.get_parameter("camera_timeout_s").value)
        self.max_age=float(self.get_parameter("frame_max_age_s").value)
        self.cv=CvBridge()
        self.frame=None
        self.frame_received=0
        self.pending=None
        self.pending_since=0
        self.result_pub=self.create_publisher(String,"/inspection/result",10)
        self.create_subscription(Image,self.get_parameter("camera_topic").value,self._image_cb,qos_profile_sensor_data)
        self.create_subscription(String,"/inspection/snapshot_request",self._req_cb,10)
        self.create_timer(.1,self._process_request)

    def _image_cb(self,msg):
        self.frame=msg
        self.frame_received=time.monotonic()

    def _req_cb(self,msg):
        try:
            request=json.loads(msg.data)
            if request.get("command_id") and request.get("task_id") and type(request.get("point_seq")) is int:
                self.store.request_snapshot(request)
        except (ValueError,AttributeError):
            self.get_logger().error("非法拍照请求")

    def _process_request(self):
        request=self.store.next_snapshot()
        if request is None:
            self.pending=None
            return
        key=(request["command_id"],request["point_seq"])
        if key!=self.pending:
            self.pending=key
            self.pending_since=time.monotonic()
        frame=self.frame
        stamp_ns=(frame.header.stamp.sec*1_000_000_000+frame.header.stamp.nanosec) if frame else 0
        fresh=(frame is not None and time.monotonic()-self.frame_received<=self.max_age
               and stamp_ns>=request.get("min_stamp_ns",0))
        if not fresh:
            if time.monotonic()-self.pending_since>=self.timeout:
                self._finish(request,None,"相机未收到到点后的新鲜图像",None)
            return
        task_id,command_id=request["task_id"],request["command_id"]
        if not all(isinstance(v,str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}",v) for v in (task_id,command_id)):
            self._finish(request,None,"拍照请求编号非法",None)
            return
        name=f"{task_id}_{request['point_seq']}_{command_id}.jpg"
        path=self.out_dir/name
        try:
            image=self.cv.imgmsg_to_cv2(frame,desired_encoding="bgr8")
            ok,encoded=cv2.imencode(".jpg",image,[cv2.IMWRITE_JPEG_QUALITY,92])
            if not ok:raise ValueError("JPEG 编码失败")
            temporary=path.with_suffix(".jpg.tmp")
            with temporary.open("wb") as file:
                file.write(encoded.tobytes())
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary,path)
        except Exception as exc:
            self._finish(request,None,f"图像保存失败: {exc}",None)
            return
        self._finish(request,f"{self.base}/api/results/img/{name}","",stamp_ns)

    def _finish(self,request,image_url,error,stamp_ns):
        result={"event_id":uuid5(NAMESPACE_URL,f"{request['command_id']}:result:{request['point_seq']}").hex,
                "command_id":request["command_id"],"task_id":request["task_id"],
                "point_seq":request["point_seq"],"place":request.get("place",""),
                "inspection":request.get("inspection"),
                "image_url":image_url,"image_source":"camera","capture_error":error,
                "captured_at":int(time.time()*1000),"camera_stamp_ns":stamp_ns}
        self.store.finish_snapshot(request,result)
        self.result_pub.publish(String(data=json.dumps(result,ensure_ascii=False)))


def main(args=None):
    rclpy.init(args=args)
    node=SnapshotNode()
    try:rclpy.spin(node)
    except KeyboardInterrupt:pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
