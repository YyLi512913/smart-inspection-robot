"""rclpy ↔ MQTT 桥接节点：遥测上行（robot/{id}/status，1Hz）+ 任务双向桥。

设计要点（防坑）：
- paho 网络循环节点独立线程（loop_start），不与 ROS executor 抢线程
- ROS 回调只缓存最新数据，MQTT 发布由 1Hz 定时器驱动，两侧解耦
- MQTT→ROS 下行经队列+定时器搬运（paho 回调线程不直接调 ROS publish）
- ROS→MQTT 上行写 SQLite，后端提交后的应用确认才删除
- 断网自治：持久化上报队列 + 自动重连 + 应用层确认
"""
import json
import math
import queue
import time
from tf2_ros import Buffer, TransformListener, TransformException
from rclpy.time import Time
from .durable_store import DurableStore, default_path

import paho.mqtt.client as mqtt
import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node
from sensor_msgs.msg import BatteryState
from std_msgs.msg import String

try:
    from rmf_fleet_msgs.msg import FleetState
    HAS_RMF = True
except ImportError:
    HAS_RMF = False

# paho-mqtt 1.x/2.x 兼容（WSL 系统装的是 1.x）
try:
    _CBV = mqtt.CallbackAPIVersion.VERSION2

    def _make_client(client_id):
        return mqtt.Client(_CBV, client_id=client_id, clean_session=False)
except AttributeError:
    def _make_client(client_id):
        return mqtt.Client(client_id=client_id, clean_session=False)


# rmf_fleet_msgs RobotMode → 平台 mode 字符串（设计文档 4.2）
RMF_MODE_MAP = {
    0: 'idle', 1: 'charging', 2: 'waiting', 3: 'navigating',
    4: 'idle', 5: 'error', 6: 'error',
}


class BridgeNode(Node):
    def __init__(self):
        super().__init__('bridge_node')
        self.declare_parameter('robot_id', 'robot1')
        self.declare_parameter('mqtt_host', '127.0.0.1')
        self.declare_parameter('mqtt_port', 1883)
        self.declare_parameter('odom_topic', '/odom')
        self.declare_parameter('battery_topic', '/battery_state')
        # rmf_demos 仿真不桥接 odom，改从 /fleet_states 取位姿+电量（真机留空即走 odom 路径）
        self.declare_parameter('fleet_topic', '')
        self.declare_parameter('fleet_robot_name', 'tinyRobot1')

        self.declare_parameter('global_frame', '')
        self.declare_parameter('base_frame', 'base_link')
        self.global_frame = self.get_parameter('global_frame').value
        self.base_frame = self.get_parameter('base_frame').value
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self.declare_parameter('journal_path', '')
        self.robot_id = self.get_parameter('robot_id').value
        self.store = DurableStore(self.get_parameter('journal_path').value or default_path(self.robot_id))
        self.last_uplink = (None, 0)
        self.odom_at = self.fleet_at = self.battery_at = 0
        self.create_timer(0.2, self._flush_uplink)
        host = self.get_parameter('mqtt_host').value
        port = int(self.get_parameter('mqtt_port').value)

        self.latest_odom = None
        self.latest_battery = None
        self.latest_fleet = None  # rmf_fleet_msgs 中本车的 RobotState
        self.current_task = None  # M2 由 task_runner 填充

        self.create_subscription(
            Odometry, self.get_parameter('odom_topic').value, self._odom_cb, 10)
        self.create_subscription(
            BatteryState, self.get_parameter('battery_topic').value, self._battery_cb, 10)
        fleet_topic = self.get_parameter('fleet_topic').value
        if fleet_topic and HAS_RMF:
            self.fleet_name = self.get_parameter('fleet_robot_name').value
            self.create_subscription(
                FleetState, fleet_topic, self._fleet_cb, 10)
        self.create_timer(1.0, self._publish_status)

        # ---- M2 任务桥接 ----
        # 下行：MQTT robot/{id}/task → 队列 → ROS /inspection/task（paho 线程不直接 publish）
        self.task_downlink = queue.Queue()
        self.task_pub = self.create_publisher(String, '/inspection/task', 10)
        self.create_timer(0.1, self._drain_downlink)
        # 上行：ROS /inspection/event|result → MQTT；current_task 填充 status
        self.create_subscription(String, '/inspection/event', self._event_cb, 10)
        self.create_subscription(String, '/inspection/result', self._result_cb, 10)
        self.create_subscription(
            String, '/inspection/current_task', self._current_task_cb, 10)

        # MQTT 客户端：paho 回调在独立网络线程
        self.mqtt = _make_client(client_id=f'bridge-{self.robot_id}')
        self.mqtt.on_connect = self._on_mqtt_connect
        self.mqtt.on_message = self._on_mqtt_message
        self.mqtt.reconnect_delay_set(min_delay=1, max_delay=10)
        try:
            self.mqtt.connect_async(host, port, keepalive=30)
        except Exception as e:
            self.get_logger().error(f'MQTT 连接失败（{e}），paho 将自动重试')
        self.mqtt.loop_start()
        self.get_logger().info(f'bridge_node 启动: robot_id={self.robot_id} mqtt={host}:{port}')

    def _on_mqtt_connect(self, client, userdata, flags, rc, *args):
        # paho 1.x 传 rc(int)，2.x 传 reason_code+properties
        self.get_logger().info(f'MQTT connected rc={rc}')
        client.subscribe(f'robot/{self.robot_id}/task', qos=1)
        client.subscribe(f'robot/{self.robot_id}/ack', qos=1)

    # ---------- M2 任务桥接 ----------
    def _on_mqtt_message(self, client, userdata, msg):
        """paho 网络线程回调：只入队，由 ROS 定时器搬运，避免跨线程 publish。"""
        if msg.topic.endswith('/ack'):
            try:
                payload = json.loads(msg.payload)
                if isinstance(payload.get('event_id'), str):
                    self.store.acknowledge(payload['event_id'])
            except (ValueError, TypeError, AttributeError):
                self.get_logger().error('非法应用确认')
            return
        self.task_downlink.put(msg.payload)

    def _drain_downlink(self):
        """ROS 定时器：下行任务报文转发到 /inspection/task。"""
        while True:
            try:
                raw = self.task_downlink.get_nowait()
            except queue.Empty:
                break
            out = String()
            out.data = raw.decode('utf-8', 'replace') if isinstance(raw, bytes) else str(raw)
            self.task_pub.publish(out)
            self.get_logger().info(f'下行任务转发 /inspection/task: {out.data[:120]}')

    def _event_cb(self, msg):
        """ROS /inspection/event → MQTT robot/{id}/event（QoS1，断网由 paho 排队重发）。"""
        self.store.enqueue('event', json.loads(msg.data))

    def _result_cb(self, msg):
        """ROS /inspection/result → MQTT robot/{id}/result（QoS1）。"""
        self.store.enqueue('result', json.loads(msg.data))

    def _flush_uplink(self):
        if not self.mqtt.is_connected():
            return
        item = self.store.next_message()
        if not item:
            return
        if self.last_uplink[0] == item['event_id'] and time.monotonic()-self.last_uplink[1] < 2:
            return
        # One message at a time until backend ACK preserves event ordering across topics.
        self.mqtt.publish(f"robot/{self.robot_id}/{item['kind']}", item['payload'], qos=1)
        self.last_uplink = (item['event_id'], time.monotonic())


    def _current_task_cb(self, msg):
        """task_runner 上报当前任务号，填充 status 报文 current_task 字段。"""
        try:
            self.current_task = json.loads(msg.data)
        except json.JSONDecodeError:
            self.current_task = None

    def _odom_cb(self, msg):
        self.latest_odom = msg
        self.odom_at = time.monotonic()

    def _battery_cb(self, msg):
        self.latest_battery = msg
        self.battery_at = time.monotonic()

    def _fleet_cb(self, msg):
        """从 FleetState 里挑出本车（按 fleet_robot_name 匹配）"""
        for rs in msg.robots:
            if rs.name == self.fleet_name:
                self.latest_fleet = rs
                self.fleet_at = time.monotonic()
                return

    @staticmethod
    def _yaw_from_quat(q):
        """四元数取偏航角（绕 z 轴旋转分量）"""
        siny = 2.0 * (q.w * q.z + q.x * q.y)
        cosy = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        return math.atan2(siny, cosy)

    def _publish_status(self):
        """按设计文档 4.2 遥测 Schema 组 JSON 上行。odom 路径优先，无 odom 用 fleet 数据。"""
        payload = {
            'ts': int(self.get_clock().now().nanoseconds // 1_000_000),
            'pos': None,
            'battery': None,
            'mode': 'idle',
            'current_task': self.current_task,
        }
        if self.latest_odom is not None and time.monotonic()-self.odom_at < 5:
            p = self.latest_odom.pose.pose
            v = self.latest_odom.twist.twist.linear
            speed = math.hypot(v.x, v.y)
            payload['pos'] = {
                'x': round(p.position.x, 3),
                'y': round(p.position.y, 3),
                'yaw': round(self._yaw_from_quat(p.orientation), 3),
            }
            payload['mode'] = 'navigating' if speed > 0.02 else 'idle'
        elif self.latest_fleet is not None and time.monotonic()-self.fleet_at < 5:
            loc = self.latest_fleet.location
            payload['pos'] = {
                'x': round(loc.x, 3),
                'y': round(loc.y, 3),
                'yaw': round(loc.yaw, 3),
            }
            payload['mode'] = RMF_MODE_MAP.get(self.latest_fleet.mode.mode, 'idle')
        if self.latest_battery is not None and time.monotonic()-self.battery_at < 5:
            # ROS BatteryState.percentage 约定 0~1，转百分数
            payload['battery'] = round(self.latest_battery.percentage * 100.0, 1)
        elif self.latest_fleet is not None and time.monotonic()-self.fleet_at < 5:
            # rmf_fleet_msgs RobotState.battery_percent 已是百分数
            payload['battery'] = round(float(self.latest_fleet.battery_percent), 1)
        if self.global_frame:
            try:
                transform = self.tf_buffer.lookup_transform(self.global_frame, self.base_frame, Time())
                stamp = transform.header.stamp
                age = (self.get_clock().now().nanoseconds - stamp.sec*1_000_000_000 - stamp.nanosec)/1e9
                if age > 2.0 or age < -0.5:
                    return
                t = transform.transform
                payload['pos'] = {'x': round(t.translation.x,3), 'y': round(t.translation.y,3),
                                  'yaw': round(self._yaw_from_quat(t.rotation),3)}
                payload['coordinate_frame'] = self.global_frame
            except TransformException:
                return
        if payload['pos'] is not None:
            self.mqtt.publish(f'robot/{self.robot_id}/status', json.dumps(payload), qos=0)


def main(args=None):
    rclpy.init(args=args)
    node = BridgeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.mqtt.disconnect()
        node.mqtt.loop_stop()
        node.destroy_node()
        rclpy.shutdown()
