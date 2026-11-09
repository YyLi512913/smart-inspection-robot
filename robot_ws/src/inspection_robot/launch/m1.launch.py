"""M1 启动文件：bridge + 假电量。

仿真若已自带 /battery_state，用 use_fake_battery:=false 关掉假电量节点。
"""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('robot_id', default_value='robot1'),
        DeclareLaunchArgument('mqtt_host', default_value='127.0.0.1'),
        DeclareLaunchArgument('use_fake_battery', default_value='true'),
        DeclareLaunchArgument('fleet_topic', default_value='/fleet_states'),
        DeclareLaunchArgument('fleet_robot_name', default_value='tinyRobot1'),
        Node(
            package='inspection_robot',
            executable='bridge_node',
            name='bridge_node',
            parameters=[{
                'robot_id': LaunchConfiguration('robot_id'),
                'mqtt_host': LaunchConfiguration('mqtt_host'),
                'fleet_topic': LaunchConfiguration('fleet_topic'),
                'fleet_robot_name': LaunchConfiguration('fleet_robot_name'),
            }],
            output='screen',
        ),
        Node(
            package='inspection_robot',
            executable='battery_node',
            name='battery_node',
            condition=IfCondition(LaunchConfiguration('use_fake_battery')),
            output='screen',
        ),
    ])
