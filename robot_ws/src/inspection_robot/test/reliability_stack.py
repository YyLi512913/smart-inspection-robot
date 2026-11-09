import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import rclpy
from rclpy.executors import SingleThreadedExecutor
from inspection_robot.task_runner import TaskRunner
from inspection_robot.bridge_node import BridgeNode
from inspection_robot.snapshot_node import SnapshotNode

directory = tempfile.TemporaryDirectory(prefix='inspection-reliability-')
rclpy.init(args=['--ros-args','-p','robot_id:=reliabilitytest','-p','mqtt_port:=18884',
                '-p','journal_path:='+directory.name+'/journal.sqlite3',
                '-p','output_dir:='+directory.name+'/images'])
nodes = [TaskRunner(), BridgeNode(), SnapshotNode()]
executor = SingleThreadedExecutor()
for node in nodes:
    executor.add_node(node)
print('RELIABILITY_ROS_READY', flush=True)
try:
    executor.spin()
except KeyboardInterrupt:
    pass
finally:
    for node in nodes:
        if hasattr(node,'mqtt'):
            node.mqtt.disconnect()
            node.mqtt.loop_stop()
        node.destroy_node()
    rclpy.shutdown()
    directory.cleanup()
