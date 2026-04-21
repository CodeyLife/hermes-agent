必须遵守的规则
    
1. 复杂任务开始前，必须先调用 `task_context_bundle(...)`。
2. 如上下文不足，再按需调用 `skill_view_safe(...)` 或 `session_recall_search(...)`。
3. 规划任务前先调用 `plan_skill_read()`。
4. 形成方案后，必须调用 `plan(...)` 落盘；需要修改时再用 `plan_read(...)` / `plan_update(...)`。
5. 默认不写入记忆或技能；仅当内容是稳定事实、长期约定、明确用户偏好，且大概率跨任务复用时，调用 `memory_write(...)`。
6. 仅当本次形成了“已验证”的可复用流程、重复修复模式，或确认需要改进现有技能时，才调用 `skill_create_or_patch(...)`。
