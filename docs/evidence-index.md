# 验证证据索引

本页帮助评审区分当前实现、实际仿真联调和协议测试。没有以测试数量推导识别准确率、生产可用性或真机安全性。

| 能力 | 可检查的证据 | 能说明什么 | 不能说明什么 |
| --- | --- | --- | --- |
| 当前后端及 ROS 组件 | [完整批次](validation/runs/20261109T083843Z-dc0bee62/report.md) · [JSON](validation/runs/20261109T083843Z-dc0bee62/report.json) | 117 项后端、9 项 ROS 组件及选定故障演练通过；代码前后指纹一致 | 未在该批重跑 Gazebo 导航、真实模型或硬件 |
| 真实消息与业务闭环 | [批次故障演练](validation/runs/20261109T083843Z-dc0bee62/system-fault-acceptance.json) | HTTP 身份、MQTT、MySQL、SQLite 的断连、去重、确认和复检链路 | 机器人为协议测试端，图像判定使用确定性数据 |
| 强制中断分析进程 | [恢复验收](validation/runs/20261109T083843Z-dc0bee62/vision-recovery-acceptance.json) | 新进程可接回任务，旧 token 不能提交，告警不重复 | 模型为替身，测试租约为 2 秒，生产为 60 秒 |
| 启动、停止与故障状态 | [运行验收](validation/runs/20261109T083843Z-dc0bee62/operations-acceptance.json) | 真实独立服务启停、重复启动、静态前端和依赖故障就绪码 | 不是持续负载、灾备或公网部署认证 |
| 三设备相机差异 | [设备场景记录](validation/equipment-acceptance.json) · [说明与照片](equipment-inspection.md) | Gazebo 中三台设备的正常/异常可见状态生成不同真实相机图，并完成模型分析 | 六张合成图不能估计工业识别准确率；柜门仍漏检 |
| 压力表异常处理复检 | [联调记录](validation/alerts-live-acceptance.json) · [处理时间线截图](assets/alerts/live-timeline.png) | 1.20 MPa 异常，经人工处理及 0.45 MPa 关联复检关闭 | 早于登录接入，历史操作人不是后补的登录身份 |
| 保留的视觉失败 | [首轮管路漏检](validation/equipment-first-pass-missed-leak.json) | 视野和几何表达影响识别，修正后重新拍摄验证 | 不能仅以第二轮通过认定误报漏报率达标 |
| 保留的工具链失败 | [首次失败批次](validation/runs/20261109T083446Z-a0b4df3d/report.md) | ROS 路径转换失败时没有把未运行的检查计为通过 | 该批不能作为完整系统通过证据 |

架构和实现入口见 [architecture.md](architecture.md)。复跑方式与隔离要求见 [system-acceptance.md](system-acceptance.md)。报告以批次文件为准，首页测试数是该批次的结果，后续改动需要产生新批次。

当前资料包含旧阶段文档，旧文档中的 RMF、样例图、未鉴权或内存推理队列描述只反映当时状态；请同时查看文档顶部的历史标记和当前项目首页。
