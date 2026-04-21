必须遵守的规则

1. 复杂任务开始前，必须先调用 `task_context_bundle(...)`。
2. 如上下文不足，再按需调用 `skill_view_safe(...)` 或 `session_recall_search(...)`。
3. 规划任务前先调用 `plan_skill_read()`。
4. 在最终回复前，必须依次确认：
- [ ] 本次任务是否已完成
- [ ] 是否存在可复用稳定事实可写入 `memory_write(...)`
- [ ] 是否存在已验证流程/模式可写入 `skill_create_or_patch(...)`
- [ ] 若都没有，内部记为“已检查，暂无可沉淀经验”
