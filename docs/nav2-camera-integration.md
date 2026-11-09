# 第二阶段：Nav2 与实时相机

> 阶段历史记录：下文描述该阶段的方案、测试数与未完成项，不等同于当前状态。当前入口、权限和持久分析实现请参阅 [项目首页](../README.md) 与 [最新验收说明](system-acceptance.md)。

日期：2026-11-09。沿用第一阶段协议 v2、数据库占用与机器人磁盘队列。

## 实现

主入口：scripts/start_nav2.sh / inspection_robot 的 nav2_factory.launch.py。

- 工厂地图：scripts/gen_nav2_factory.py 复用原工厂墙体坐标，同时生成 Gazebo SDF 与 5cm Nav2 PGM 占用图；车间、配电房、服务器机房点位保持原坐标。
- 仿真：自定义差速机器人，物理碰撞、720 线激光、640×480 RGB 相机。没有通过改位置来模拟导航。
- 定位与导航：AMCL + map/odom/base_link TF；Navfn A* 全局规划、DWB 局部控制、Nav2 默认重规划/恢复行为与速度平滑器。
- 仿真里程计：Gazebo OdometryPublisher 根据物理位姿输出 /odom 和 odom→base_link；编码器估计独立输出 /wheel_odom、/wheel_tf 作为诊断，未接到 Nav2 TF。采用此方式隔离自定义模型的轮滑偏差，不声称已解决真机轮速标定。
- 执行适配：nav2_task_runner 使用 NavigateToPose Action；目标 UUID 由 command_id + point_seq 稳定生成，发出前写入日志；回调核对任务与点位，防止旧响应污染新任务。
- 到点：只认 Action SUCCEEDED，然后等待新鲜里程计显示线速度与角速度都低于 0.025，再提交拍照请求；保存图像结果前不前往下一点。
- 朝向：车间看向工作台，配电房看向柜体，服务器机房看向机柜。朝向为仿真点位的预置业务配置。
- 取消：指定 UUID 撤销，不能以“取消请求已受理”当成已经停下。等待导航终态与新鲜零速遥测后才发送 cancel_confirmed。
- 重启：执行节点恢复日志后报告需要核对；可以通过持久化 UUID 查询导航终态并撤销。UNKNOWN 不当作成功撤销，未确认前保留占用。
- 进程唯一性：同一 SQLite 日志有独占执行锁，重复启动执行节点会报错；桥接与相机仍共享该日志。
- 相机：sensor_msgs/Image + SensorDataQoS + cv_bridge。图像时间必须不早于到点请求，接收时间也必须新鲜。JPEG 原子写入后，结果入队与请求完成标记一同提交。
- 相机错误：8 秒无有效新帧会记录 capture_error，当前任务失败，禁止用样例图或空图冒充成功。
- 前端：结果标注“实时相机”或“历史采集”；采集失败有独立提示。
- 配置：新增增量迁移只把未修改的 office 默认充电桩转换为 factory 的 (2,-3)，保留运维自定义坐标。

## 运行

首次依赖安装与完整运行见项目 README.md。
当前机器已安装 Nav2；在 WSL 中：

```bash
bash /mnt/f/robot-inspection/scripts/start_nav2.sh
# 可选图形窗口
bash /mnt/f/robot-inspection/scripts/start_nav2.sh gui:=true
```

默认使用 robot1、MQTT 1883、相机 /camera/image_raw，结果目录为 Windows 的 backend/static/results。
launch 参数支持 robot_id、mqtt_host、mqtt_port、journal_path、output_dir、gui。
默认软件渲染，WSL 下已验证；可通过 LIBGL_ALWAYS_SOFTWARE=0 使用合适的 GPU 驱动。

后端、MQTT、Redis、MySQL、前端按原配置运行。旧的 m2.launch.py 是 RMF 回归入口，不能与 Nav2 主入口混跑。

## 可复现测试

后端：

```text
cd backend
.venv\Scripts\python.exe -m unittest discover -s tests
```

ROS 组件与地图连通性（无需 Gazebo、不会移动机器人）：

```bash
bash /mnt/f/robot-inspection/scripts/test_nav2_components.sh
```

真实 Gazebo 验收使用独立 ROS_DOMAIN_ID=212、MQTT 18885 与临时 MySQL 数据库，避免污染业务数据。
先在 WSL 开启测试 broker：

```bash
mosquitto -p 18885
```

另一个 WSL 终端启动测试栈：

```bash
export ROS_DOMAIN_ID=212
export GZ_PARTITION=inspection-nav2-acceptance
bash /mnt/f/robot-inspection/scripts/start_nav2.sh robot_id:=nav2test mqtt_port:=18885 journal_path:=/tmp/inspection-nav2-acceptance/journal.sqlite3 output_dir:=/mnt/f/robot-inspection/.tmp/nav2-camera
```

Windows 的 backend 目录运行：

```text
.venv\Scripts\python.exe scripts/nav2_acceptance.py
# 实际调用配置的视觉模型，并检查结果入库
.venv\Scripts\python.exe scripts/nav2_acceptance.py --place workshop --vlm
```

默认测试不调用外部模型；--vlm 才调用现有配置的网关。测试会创建和清理自身的临时数据库，需要账号具备建库权限。测试图片保留在 .tmp/nav2-camera。
测试结束后用 Ctrl+C 关闭测试 launch 与测试 broker。

生成静态场景：

```bash
python3 /mnt/f/robot-inspection/scripts/gen_nav2_factory.py
```

动态障碍脚本：scripts/nav2_obstacle.sh，可在当前 GZ_PARTITION 的服务器机房门口添加/删除障碍。该脚本提供手工复现入口，不等于已经完成全部动态避障场景验收。

## 验收证据

- 后端原有 27 项回归通过。
- 新增 9 项 ROS 组件测试通过，覆盖 Action 成功/失败、取消终态与零速、相机超时/新鲜帧、重复执行进程，以及带机器人半径膨胀后的全部点位连通性。
- 真实 Gazebo + Nav2 + MQTT + MySQL：走廊 → 穿门进入车间 → 每点停稳保存相机图像 → DONE。
- 真实行驶中取消：Action CANCELED，速度归零，后端收到确认并释放占用。
- 多点验收为隔离确定性测试，关闭外部模型调用；另执行 --vlm 场景，验证实际图片经模型分析保存至 inspection_result。
- 前端生产构建通过，仍有 Element Plus 大包提示。
- 业务库 25 条历史任务的状态数量保持不变。

证据文件位于 docs/validation，实际相机图位于 docs/assets。
简单设备几何适合验证采集与通信，不能用于评价工业缺陷识别准确率。

## 尚未完成

- 真机部署与实际编码器/IMU 融合标定。
- 主动回桩与充电恢复闭环（当前电量为模拟数据）。
- 异常主动推送、长期轨迹存储及回放。
- VLM 持久化推理队列、质量评估与部署鉴权。

## 官方参考

- [Nav2 Gazebo 里程计接入](https://docs.nav2.org/jazzy/configuration_and_development/first_time_robot_setup_guide/odom/setup_odom_gz/)
- [Gazebo Harmonic 传感器](https://gazebosim.org/docs/harmonic/sensors/)
- [Gazebo OdometryPublisher](https://gazebosim.org/api/sim/8/classgz_1_1sim_1_1systems_1_1OdometryPublisher.html)

相机和导航测试共 9 项，加上后端 27 项，合计 36 项自动测试。

config/nav2.yaml 基于 nav2_bringup Jazzy 默认参数（Apache-2.0）调整。
