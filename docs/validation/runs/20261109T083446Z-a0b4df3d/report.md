# 系统验收报告

批次：20261109T083446Z-a0b4df3d
生成时间：2026-11-09T08:36:18.157986+00:00
总体状态：failed

本批次使用独立数据库、独立消息服务与独立端口；不向运行中的机器人发送任务，不调用付费模型。

| 阶段 | 结果 | 耗时（秒） | 日志 |
| --- | --- | --- | --- |
| frontend_build | passed | 15.66 | [frontend_build.log](frontend_build.log) |
| backend_regression | passed | 41.86 | [backend_regression.log](backend_regression.log) |
| protocol_faults | passed | 10.09 | [protocol_faults.log](protocol_faults.log) |
| vision_process_recovery | passed | 9.23 | [vision_process_recovery.log](vision_process_recovery.log) |
| service_lifecycle | passed | 14.61 | [service_lifecycle.log](service_lifecycle.log) |
| ros_components | failed | 0.0 | [ros_components.log](ros_components.log) |

## 覆盖范围

- 真实 HTTP 身份校验、MySQL 事务、MQTT 断连与重连、SQLite 消息落盘。
- 重复指令不重复执行、照片应用确认前保留、取消确认前保留占用、延迟指令不复活已取消任务。
- 视觉重试、结果和告警原子提交、人工处理与关联复检关闭、分析进程强制中断后的接管。
- 服务正常启动/停止、依赖故障下就绪检查、请求编号和 JSON 日志。
- 机器人协议由测试端替代；视觉结果为确定性测试数据，不能代表导航效果或识别准确率。
- 故障演练中只加速重试等待时间；独立进程恢复用 2 秒测试租约，生产租约仍为 60 秒。

本次是否运行 ROS 组件测试：是

## 历史场景证据（未在本批重跑）

- docs\validation\equipment-acceptance.json（SHA-256 84b05c5aa9244c34eed300d358a5257a3cdff2e63c4e7660c83c89cacee53032）
- docs\validation\alerts-live-acceptance.json（SHA-256 83201cfaea20994511205e63b85e5adc7a5b3f50499fccdab76681c5821d62c8）
- docs\validation\equipment-first-pass-missed-leak.json（SHA-256 8cbdf6eb5679aabcb52ed65786811f1ce6e6ce4252484380689af8b9e7ecc6c5）

后端回归测试数：116

具体失败原因见对应阶段日志；失败或缺少记录不会沿用上一批的通过结果。
