# Plan — Flutter 考试端 SignalR 数据接收后 UI 同步更新完整性分析

## Goal

全面分析 Flutter 考试端收到 SignalR 数据后，是否同步更新了界面，覆盖所有消息类型、所有分支路径，识别潜在的 UI 不同步问题

## Current context / assumptions

- Flutter 考试端使用 GetX 状态管理 + SignalR 实时通信
- 核心 Controller：ExamController、ExamRuntimeController、TriageController
- 核心 Widget：ExamRoomPage、BedSlotWidget、TriagePanel
- 后端通过 SignalR Hub 广播 StateSyncV2、TriageUpdated、ClockSync 等消息
- 前端通过 subscribe 模式注册监听，使用 Obx 实现响应式 UI

## Proposed approach

1. 梳理 SignalR 数据接收的完整链路（后端广播 → 前端解析 → Controller 更新 → Widget 渲染）
2. 分析每条 SignalR 消息类型的处理分支和 UI 同步情况
3. 识别数据更新到 UI 渲染之间的所有潜在断点
4. 评估 GetX 响应式机制在各场景下的触发可靠性
5. 输出完整的问题清单和风险评级

## Step-by-step plan

- 1. 分析 SignalR 每种消息类型的接收和处理流程
- 2. 追踪数据从 SignalR Service 到 Controller 的传递路径
- 3. 检查 Controller 中 Rx 变量的更新是否正确触发 UI 重建
- 4. 验证 Widget 层 Obx/GetBuilder 是否正确监听了对应的 Rx 变量
- 5. 识别所有可能导致 UI 不同步的分支和边界条件
- 6. 评估重连、初始化、销毁等生命周期场景下的同步可靠性
- 7. 汇总问题清单并给出修复建议

## Files likely to change

- exam_system_flutter/lib/core/services/signalr_service.dart
- exam_system_flutter/lib/presentation/controllers/exam_controller.dart
- exam_system_flutter/lib/presentation/controllers/exam_runtime_controller.dart
- exam_system_flutter/lib/presentation/controllers/triage_controller.dart
- exam_system_flutter/lib/presentation/pages/student/exam_room_page.dart
- exam_system_flutter/lib/presentation/widgets/bed_slot_widget.dart
- exam_system_flutter/lib/presentation/widgets/triage_panel.dart
- exam_system_flutter/lib/data/models/exam/exam_runtime_snapshot.dart
- exam_system_flutter/lib/data/models/exam/exam_progress.dart
- BackEnd/Hubs/ExamSyncHub.cs

## Tests / validation

- 验证 StateSyncV2 消息到达后床位区域 UI 是否立即更新
- 验证 TriageUpdated 消息到达后分检区面板是否立即更新
- 验证 ClockSync 消息到达后倒计时是否同步
- 验证网络断开重连后 UI 状态是否与服务端一致
- 验证多客户端同时操作时 UI 同步的一致性

## Risks / tradeoffs

- GetX Worker/ever 订阅可能在特定时机未触发
- Obx 包裹范围可能遗漏某些需要更新的子组件
- 异步操作（如案例数据加载）可能导致 UI 显示旧数据
- 状态机转换可能与服务端状态不一致
- 重连后的状态恢复可能不完整
- 多个 Controller 之间的数据同步可能存在时序问题
