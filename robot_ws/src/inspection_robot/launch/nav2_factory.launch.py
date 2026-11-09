"""Self-contained factory simulation + AMCL/Nav2 + inspection business nodes."""
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, TimerAction
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node


def generate_launch_description():
    share=Path(get_package_share_directory("inspection_robot"))
    gz_share=Path(get_package_share_directory("ros_gz_sim"))
    sim={"use_sim_time":True}
    nav_params=str(share/"config/nav2.yaml")
    world=str(share/"worlds/factory.sdf")
    common={"use_sim_time":True,"robot_id":LaunchConfiguration("robot_id"),
            "journal_path":LaunchConfiguration("journal_path")}
    nodes=[
        DeclareLaunchArgument("robot_id",default_value="robot1"),
        DeclareLaunchArgument("mqtt_host",default_value="127.0.0.1"),
        DeclareLaunchArgument("mqtt_port",default_value="1883"),
        DeclareLaunchArgument("journal_path",default_value=""),
        DeclareLaunchArgument("output_dir",default_value="/mnt/f/robot-inspection/backend/static/results"),
        DeclareLaunchArgument("gui",default_value="false"),
        IncludeLaunchDescription(PythonLaunchDescriptionSource(str(gz_share/"launch/gz_sim.launch.py")),
            launch_arguments={"gz_args":PythonExpression(["'-r ",world,"' if '",LaunchConfiguration("gui"),"' == 'true' else '-r -s --headless-rendering ",world,"'"])}.items()),
        Node(package="robot_state_publisher",executable="robot_state_publisher",
             parameters=[sim,{"robot_description":(share/"urdf/inspection_robot.urdf").read_text()}]),
        Node(package="ros_gz_bridge",executable="parameter_bridge",
             parameters=[sim,{"config_file":str(share/"config/gz_bridge.yaml")}]),
        Node(package="nav2_map_server",executable="map_server",name="map_server",
             parameters=[sim,{"yaml_filename":str(share/"maps/factory.yaml")}]),
        Node(package="nav2_amcl",executable="amcl",name="amcl",parameters=[nav_params,sim]),
        Node(package="nav2_lifecycle_manager",executable="lifecycle_manager",name="lifecycle_manager_localization",
             parameters=[sim,{"autostart":True,"node_names":["map_server","amcl"]}]),
    ]
    navigation=[
        ("nav2_controller","controller_server","controller_server",[("cmd_vel","cmd_vel_nav")]),
        ("nav2_planner","planner_server","planner_server",[]),
        ("nav2_smoother","smoother_server","smoother_server",[]),
        ("nav2_behaviors","behavior_server","behavior_server",[("cmd_vel","cmd_vel_nav")]),
        ("nav2_bt_navigator","bt_navigator","bt_navigator",[]),
        ("nav2_velocity_smoother","velocity_smoother","velocity_smoother",
         [("cmd_vel","cmd_vel_nav"),("cmd_vel_smoothed","cmd_vel")]),
    ]
    for package,executable,name,remappings in navigation:
        nodes.append(Node(package=package,executable=executable,name=name,
                          parameters=[nav_params,sim],remappings=remappings,output="screen"))
    nodes.append(Node(package="nav2_lifecycle_manager",executable="lifecycle_manager",name="lifecycle_manager_navigation",
        parameters=[sim,{"autostart":True,"node_names":[n[2] for n in navigation]}],output="screen"))
    nodes.extend([
        Node(package="inspection_robot",executable="battery_node",parameters=[sim]),
        Node(package="inspection_robot",executable="bridge_node",parameters=[common,{
            "mqtt_host":LaunchConfiguration("mqtt_host"),"mqtt_port":LaunchConfiguration("mqtt_port"),
            "global_frame":"map","base_frame":"base_link"}]),
        Node(package="inspection_robot",executable="snapshot_node",parameters=[common,{
            "output_dir":LaunchConfiguration("output_dir")}]),
        TimerAction(period=8.0,actions=[Node(package="inspection_robot",executable="nav2_task_runner",parameters=[common],output="screen")])
    ])
    return LaunchDescription(nodes)
