"""ament_python 包定义"""
from setuptools import setup
from glob import glob

package_name = 'inspection_robot'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', glob('launch/*.launch.py')),
        ('share/' + package_name + '/config', glob('config/*')),
        ('share/' + package_name + '/maps', glob('maps/*')),
        ('share/' + package_name + '/worlds', glob('worlds/*')),
        ('share/' + package_name + '/urdf', glob('urdf/*')),
        ('share/' + package_name + '/assets/equipment', glob('assets/equipment/*')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='yanyang',
    maintainer_email='yanyang@local',
    description='巡检机器人业务层：MQTT 桥接 + 假电量',
    license='MIT',
    entry_points={
        'console_scripts': [
            'bridge_node = inspection_robot.bridge_node:main',
            'battery_node = inspection_robot.battery_node:main',
            'task_runner = inspection_robot.task_runner:main',
            'nav2_task_runner = inspection_robot.nav2_task_runner:main',
            'snapshot_node = inspection_robot.snapshot_node:main',
        ],
    },
)
