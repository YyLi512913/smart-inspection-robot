"""假电量节点（纯演示用）：订阅 /odom 估算耗电，发布 /battery_state。

规则（对齐设计文档 5.1）：基础耗电 0.05%/s，导航中翻倍；<20% 记 low_battery 警告（M2 接事件上报）。
若仿真自带真实电池 topic，不要启动本节点——把 bridge 的 battery_topic 指过去即可。
"""
import math

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node
from sensor_msgs.msg import BatteryState


class BatteryNode(Node):
    def __init__(self):
        super().__init__('battery_node')
        self.declare_parameter('initial_level', 100.0)  # 百分数
        self.declare_parameter('drain_per_sec', 0.05)   # 基础耗电 %/s
        self.level = min(100.0, max(0.0, float(self.get_parameter('initial_level').value)))
        self.drain = float(self.get_parameter('drain_per_sec').value)
        self.moving = False
        self.warned = False
        self.last_ts = self.get_clock().now()

        self.create_subscription(Odometry, '/odom', self._odom_cb, 10)
        self.pub = self.create_publisher(BatteryState, '/battery_state', 10)
        self.create_timer(1.0, self._tick)
        self.get_logger().info(f'battery_node 启动: 初始电量 {self.level:.1f}%')

    def _odom_cb(self, msg):
        v = msg.twist.twist.linear
        self.moving = math.hypot(v.x, v.y) > 0.02

    def _tick(self):
        now = self.get_clock().now()
        dt = (now - self.last_ts).nanoseconds / 1e9
        self.last_ts = now
        self.level = max(0.0, self.level - self.drain * (2.0 if self.moving else 1.0) * dt)
        if self.level < 20.0 and not self.warned:
            self.warned = True
            self.get_logger().warn(f'低电量 {self.level:.1f}%（low_battery 事件 M2 接 MQTT）')

        msg = BatteryState()
        msg.header.stamp = now.to_msg()
        msg.percentage = self.level / 100.0  # ROS 约定 0~1
        msg.voltage = 11.1 + 1.5 * (self.level / 100.0)
        msg.power_supply_status = BatteryState.POWER_SUPPLY_STATUS_DISCHARGING
        msg.power_supply_technology = BatteryState.POWER_SUPPLY_TECHNOLOGY_LION
        self.pub.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = BatteryNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
