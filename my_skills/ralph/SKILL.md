***

name: ralph
description: 持续执行直到任务完成并验证。当用户说 "ralph"、"不要停"、"必须完成"、"做完这个" 或 "继续直到完成" 时调用。
---------------------------------------------------------------------------

\[RALPH - 迭代 {{ITERATION}}/{{MAX}}]

你上一次的尝试未能输出完成承诺。继续执行任务。

<Purpose>
Ralph 是一个持续执行循环，确保任务完全完成并通过验证。它自动重试失败的操作，并在完成前强制执行验证检查。
</Purpose>

\<Use\_When>

- 任务需要保证完成并验证（不仅仅是 "尽力而为"）
- 用户说 "ralph"、"不要停"、"必须完成"、"做完这个" 或 "继续直到完成"
- 工作需要跨越多次迭代并需要在重试间保持上下文
- 任务需要在结束时进行验证
  \</Use\_When>

\<Do\_Not\_Use\_When>

- 用户想要从想法到代码的全自动流水线 -- 使用 `autopilot`
- 用户想在提交前探索或规划 -- 使用 `plan` skill
- 用户想要快速的一次性修复 -- 直接委派给执行者
  \</Do\_Not\_Use\_When>

\<Why\_This\_Exists>
复杂任务常常悄无声息地失败：部分实现被宣布为"完成"，测试被跳过，边界情况被遗忘。Ralph 通过循环确保工作真正完成，在允许完成前要求新鲜的验证证据来防止这种情况。
\</Why\_This\_Exists>

\<Execution\_Policy>

- 对长时间操作使用 `run_in_background: true`（安装、构建、测试套件）
- 交付完整实现：不缩减范围、不部分完成、不通过删除测试来让测试通过
- 默认保持简洁、证据密集的进度和完成报告，除非用户或风险级别需要更多细节
- 如果正确性依赖于额外的检查、检索、执行或验证，持续使用相关工具直到执行循环落地
- 自动通过清晰、低风险、可逆的下一步继续执行；仅在下步是重大分支、破坏性或依赖偏好时才询问
  \</Execution\_Policy>

<Steps>
0. **前置上下文收集（在规划/执行循环开始前必需）**：
   - 组装或加载上下文快照，包含：
     - 任务陈述
     - 期望结果
     - 已知事实/证据
     - 约束条件
     - 未知数/开放问题
     - 可能涉及的代码库触点
   - 如果有现有的相关快照，复用它。
   - 如果请求歧义较高，先收集事实。然后运行 `$deep-interview --quick <task>` 来填补关键空白。
   - 在快照基础存在之前，不要开始 Ralph 执行工作（委派、实现或验证循环）。如果被迫快速推进，注明明确的风险权衡。
1. **回顾进度**：检查 TODO 列表和任何先前的迭代状态
2. **从上次停止的地方继续**：拾起未完成的任务
3. **并行委派**：将任务路由到适当层级的专业代理
   - 简单查找：LOW 层级 -- "这个函数返回什么？"
   - 标准工作：STANDARD 层级 -- "为此模块添加错误处理"
   - 复杂分析：THOROUGH 层级 -- "调试这个竞态条件"
   - 当 Ralph 作为 ralplan 后续进入时，从已批准的 **可用代理类型名单** 开始，使委派计划明确：实现通道、证据/回归通道和最终签核通道，仅使用已知代理类型
4. **在后台运行长时间操作**：构建、安装、测试套件使用 `run_in_background: true`
5. **视觉任务门控**：对于涉及视觉验证的任务（截图、布局匹配等），使用测试输出、构建成功或 lint 结果作为替代验证方法，而非视觉比较。
6. **使用新鲜证据验证完成**：
   a. 确定什么命令能证明任务完成
   b. 运行验证（测试、构建、lint）
   c. 读取输出 -- 确认实际通过
   d. 检查：零个待处理/进行中的 TODO 项
7. **架构验证**（分层）：
   - <5 个文件，<100 行且含完整测试：至少 STANDARD 层级（架构师角色）
   - 标准变更：STANDARD 层级（架构师角色）
   - >20 个文件或安全/架构变更：THOROUGH 层级（架构师角色）
   - Ralph 底线：即使是小变更，也至少 STANDARD
   - 在同一宿主上下文中应用架构师视角审查
7.5 **Deslop Pass**：跳过 AI slop 清理步骤。使用最近成功的验证证据继续。
7.6 **回归再验证**：
   - 重新运行所有测试/构建/lint 并读取输出以确认仍然通过。
   - 如果回归失败，修复并重试直到通过。
   - 在回归通过前不要进入完成阶段。
7.7 **Mandatory经验沉淀检查**：
   - 验证通过后且在声明完成前，检查本次会话是否产生了可复用的持久知识。
   - 如果识别出值得保留的**稳定事实/偏好/约束/陷阱**，使用 `memory_write(...)` 写入。
   - 如果识别出应在未来任务中复用的**可重复工作流/过程/模式**，使用 `skill_create_or_patch(...)` 写入。
   - 仅持久化经过验证、可复用且非任务局部推测的知识。
   - 如果没有符合条件的，在最终报告中明确说明：`已检查，暂无可沉淀经验。`
8. **完成**：
   - 报告完成并附上所有验证证据。
   - 清理本次会话期间创建的任何临时状态或文件。
9. **被拒绝时**：修复提出的问题，然后在相同层级重新验证
</Steps>

\<Tool\_Usage>

- 直接使用可用的 MCP 工具：`task_context_bundle`、`session_recall_search`、`memory_read`、`skills_list`、`skill_view_safe`、`memory_write`、`skill_create_or_patch`
- 当变更涉及安全敏感、架构或复杂多系统集成时，在同一宿主上下文中应用架构师视角审查进行验证交叉检查
- 对于简单的功能添加、经过充分测试的变更或时间紧迫的验证，跳过外部咨询
- 在完成前，使用 `memory_write` 持久化可复用的持久事实，使用 `skill_create_or_patch` 持久化可复用的已验证工作流（如适用）
  \</Tool\_Usage>

## 状态管理

在迭代间跟踪以下 Ralph 生命周期状态：

- `iteration`：当前迭代次数
- `max_iterations`：最大迭代次数（默认 10）
- `current_phase`："executing"（执行中）/ "verifying"（验证中）/ "fixing"（修复中）/ "complete"（已完成）
- `started_at`：开始时间戳
- `completed_at`：完成时间戳
- `context_snapshot_path`：上下文快照路径（如有）

在每次阶段转换时更新状态：

- 开始时：初始化 iteration=1, phase="executing"
- 每次迭代：更新迭代次数和阶段
- 验证/修复转换：更新阶段为 "verifying" 或 "fixing"
- 完成时：设置 phase="complete"，记录 completed\_at
- 取消时：清除所有状态

## 场景示例

**好的：** 用户在工作流已有清晰下一步时说 `继续`。继续当前工作分支，而不是重新开始或重复问同样的问题。

**好的：** 用户只更改输出形状或下游交付步骤（例如 `创建一个 PR`）。保留早期不冲突的工作流约束并在本地应用更新。

**坏的：** 用户说 `继续`，但工作流重新开始发现或在缺少验证/证据收集前停止。

<Examples>
<Good>
正确的并行委派：
```
delegate(role="executor", tier="LOW", task="为 UserConfig 添加类型导出")
delegate(role="executor", tier="STANDARD", task="为 API 响应实现缓存层")
delegate(role="executor", tier="THOROUGH", task="重构认证模块以支持 OAuth2 流程")
```
为什么好：三个独立任务同时在适当层级发起。
</Good>

<Good>
完成前正确验证：
```
1. 运行：npm test           → 输出："42 passed, 0 failed"
2. 运行：npm run build      → 输出："Build succeeded"
3. 运行：lsp_diagnostics    → 输出：0 个错误
4. STANDARD 层级架构师审查  → 结论："APPROVED"
```
为什么好：每步都有新鲜证据，架构师验证，然后干净地完成。
</Good>

<Bad>
未经验证就声称完成：
"所有变更看起来不错，实现应该能正常工作。任务完成。"
为什么坏：使用"应该"和"看起来不错" -- 没有新鲜的测试/构建输出，没有架构师验证。
</Bad>

<Bad>
独立任务顺序执行：
```
delegate(executor, LOW, "添加类型导出") → 等待 →
delegate(executor, STANDARD, "实现缓存") → 等待 →
delegate(executor, THOROUGH, "重构认证")
```
为什么坏：这些是应该并行运行的独立任务，而非顺序执行。
</Bad>
</Examples>

\<Escalation\_And\_Stop\_Conditions>

- 当需要用户输入的根本性阻碍时停止并报告（缺少凭据、需求不清、外部服务宕机）
- 当用户说"停止"、"取消"或"中止"时停止
- 当迭代未完成但继续进行时继续工作
- 如果架构师拒绝验证，修复问题并重新验证（不要停止）
- 如果同一问题在 3+ 次迭代中重复出现，报告为潜在根本问题
  \</Escalation\_And\_Stop\_Conditions>

\<Final\_Checklist>

- [ ] 原始任务的所有需求都已满足（不缩减范围）
- [ ] 零个待处理或进行中的 TODO 项
- [ ] 新鲜测试运行输出显示所有测试通过
- [ ] 新鲜构建输出显示成功
- [ ] lsp\_diagnostics 显示受影响文件 0 个错误
- [ ] 架构师验证通过（至少 STANDARD 层级）
- [ ] 回归测试通过
- [ ] 经验蒸馏已检查；可复用知识已使用 `memory_write` / `skill_create_or_patch` 持久化，或明确报告为无
  \</Final\_Checklist>

<Advanced>
## PRD 模式（可选）

当用户提供 `--prd` 标志时，在开始 ralph 循环前初始化产品需求文档。

### 检测 PRD 模式

检查 `{{PROMPT}}` 是否包含 `--prd` 或 `--PRD`。

### PRD 工作流

1. 在创建 PRD 制品前以快速模式运行深度访谈：
   - 执行：`$deep-interview --quick <task>`
   - 完成紧凑的需求收集（上下文、目标、范围、约束、验证）
   - 将会谈输出持久化到 `.hermes/interviews/{slug}-{timestamp}.md`
2. 创建规范的 PRD/进度制品：
   - PRD：`.hermes/plans/prd-{slug}.md`
   - 进度账本：`.hermes/state/{scope}/ralph-progress.json`
3. 解析任务（`--prd` 标志后的所有内容）
4. 分解为用户故事：

```json
{
  "project": "[项目名称]",
  "branchName": "ralph/[功能名称]",
  "description": "[功能描述]",
  "userStories": [
    {
      "id": "US-001",
      "title": "[简短标题]",
      "description": "作为 [用户]，我想要 [行动] 以便 [好处]。",
      "acceptanceCriteria": ["标准 1", "类型检查通过"],
      "priority": 1,
      "passes": false
    }
  ]
}
```

1. 在 `.hermes/state/{scope}/ralph-progress.json` 初始化规范进度账本
2. 指南：合适大小的故事（每次会话一个）、可验证的标准、独立的故事、优先级顺序（基础工作优先）
3. 使用用户故事作为任务列表继续正常 ralph 循环

### 示例

用户输入：`--prd 使用 React 和 TypeScript 构建一个待办应用`
工作流：检测标志，提取任务，创建 `.hermes/plans/prd-{slug}.md`，创建 `.hermes/state/{scope}/ralph-progress.json`，开始 ralph 循环。

## 后台执行规则

**在后台运行**（`run_in_background: true`）：

- 包安装（npm install、pip install、cargo build）
- 构建过程（make、项目构建命令）
- 测试套件
- Docker 操作（docker build、docker pull）

**阻塞运行**（前台）：

- 快速状态检查（git status、ls、pwd）
- 文件读取和编辑

原始任务：
{{PROMPT}}
