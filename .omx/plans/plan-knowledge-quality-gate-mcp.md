# Plan: MCP 知识质量验证与过期淘汰机制

## Requirements Summary

目标是在 Hermes MCP 现有知识沉淀流程中加入“写入前审核 + 上下文加载时过期审计”的知识质量验证机制：

1. 在 MCP `memory_write` 与 `skill_create_or_patch` 写入路径前置评分/审核。
2. 审核维度包含：
   - 稳定性验证：是否来自已验证事实；是否只是临时结论/推测。
   - 可复用性评估：未来是否会复用；是否通用到足以长期保存。
   - 完整性检查：是否包含问题/方案/原因；是否有应用位置引用。
   - 冲突检测：是否与现有记忆/技能冲突；是否应替换/补丁而非新增。
3. 在 `task_context_bundle` 中增加过期知识审核机制，避免默认上下文带入过期或低质量知识。
4. 先做 MVP，不引入新依赖，不大改存储结构；保留现有工具名和协议兼容性。

## Current Code Facts

- `mcp_serve.py` 注册 MCP 工具：`memory_read`、`memory_write`、`skills_list`、`skill_view_safe`、`skill_create_or_patch`、`task_context_bundle`。关键区域：`mcp_serve.py:967-1177`。
- `memory_write` 当前只做参数检查、长度限制，然后调用 `tools.memory_tool.memory_write_v1(...)`。关键区域：`mcp_serve.py:982-1025`。
- `skill_create_or_patch` 当前直接调用 `tools.skill_manager_tool.skill_create_or_patch_v1(...)`，没有知识质量评分门禁。关键区域：`mcp_serve.py:1050-1071`。
- `task_context_bundle` 当前读取 live memory、session recall、local skills，并返回 memory/user 最近 N 条、会话命中、技能候选、hints。关键区域：`mcp_serve.py:1105-1175`。
- `tools.memory_tool.MemoryStore.add/replace` 已有注入扫描、去重、容量限制、文件锁与原子写，但没有稳定性/复用性/完整性/冲突语义评分。关键区域：`tools/memory_tool.py:105-270`、`tools/memory_tool.py:480-515`。
- `tools.skill_manager_tool` 已有技能格式校验、安全扫描、路径限制和 registry 注册，但缺少“是否值得沉淀成技能”的质量门禁。关键区域：`tools/skill_manager_tool.py:1-180`、`tools/skill_manager_tool.py:720-840`。

## Decision

采用“现有写入工具内嵌 Knowledge Quality Gate”的方案，而不是新增独立 `knowledge_commit` 工作流作为第一阶段。

也就是：

```text
memory_write(...) ─┐
                   ├─ quality gate score/review ── pass/warn/block ── existing write implementation
skill_create_or_patch(...) ─┘


task_context_bundle(...) ── daily/TTL freshness audit ── filter/annotate stale knowledge ── context bundle
```

这样可以在不改变调用方习惯的前提下，把知识审核嵌进现有流程。

## RALPLAN-DR Summary

### Principles

1. **兼容优先**：保留现有 MCP tool 名称、输入参数和主要返回结构。
2. **门禁前置**：长期知识写入前先审核，避免污染后再清理。
3. **规则优先，LLM 可选**：MVP 用确定性规则和元数据评分，不依赖外部模型。
4. **低摩擦落地**：默认不阻塞高价值写入；低分进入 warning 或 pending，而不是一刀切失败。
5. **检索安全**：`task_context_bundle` 默认不应加载已判定过期/淘汰的知识。

### Decision Drivers

1. 现有 MCP 写入口集中，适合低侵入加门禁。
2. 长期记忆/技能污染成本高于一次写入的延迟成本。
3. `task_context_bundle` 是任务前上下文入口，适合做轻量的过期检查与展示过滤。

### Viable Options

#### Option A: 在 `memory_write` / `skill_create_or_patch` 内直接加评分门禁（推荐）

**Pros**
- 不改变现有使用流程。
- 最容易保证所有 MCP 写入都经过审核。
- 改动集中在 `mcp_serve.py` 和新增小模块。

**Cons**
- 如果未来还有非 MCP 写入路径，需要额外接入。
- 返回结构需要谨慎扩展，避免破坏调用方。

#### Option B: 新增独立 `knowledge_validate` / `knowledge_commit` 工具

**Pros**
- 架构更纯粹，验证与写入解耦。
- 便于未来接入 wiki、向量库、多 agent 共识。

**Cons**
- 调用方需要改习惯，容易绕过。
- 第一阶段收益慢于直接嵌入现有工具。

#### Option C: 只在 `task_context_bundle` 做事后审计

**Pros**
- 实现最简单，不影响写入。

**Cons**
- 污染已经进入存储，只是加载时隐藏。
- 冲突和低质量知识仍会堆积。

推荐 Option A + 部分 Option C：写入前审核为主，`task_context_bundle` 做每日/TTL 复核和上下文过滤。

## Proposed Architecture

### 1. 新增知识质量模块

新增文件建议：

```text
tools/knowledge_quality.py
```

职责：

```python
score_memory_candidate(...)
score_skill_candidate(...)
check_conflicts(...)
compute_freshness(...)
load_quality_index(...)
save_quality_index(...)
audit_due_knowledge(...)
```

MVP 不新增依赖，使用 JSON 文件维护元数据索引：

```text
{HERMES_HOME}/knowledge_quality/index.json
{HERMES_HOME}/knowledge_quality/audit-log.jsonl
```

不要写到仓库 `.omx/`，因为 MCP memory/skills 是 profile-scoped runtime 数据，应跟 `get_hermes_home()`。

### 2. 写入前评分维度

建议满分 100，硬性 block 与软性 warning 分开。

#### 2.1 稳定性 `stability_score`，权重 30

检查项：

- 是否包含明确证据词：`verified`, `tested`, `observed`, `from AGENTS.md`, `from file`, `confirmed by` 等。
- 是否包含临时/推测词：`可能`, `猜测`, `临时`, `maybe`, `probably`, `workaround for now`。
- 是否是一次性任务状态：`当前正在`, `刚刚`, `这次任务`, `today only`。

建议：

```text
临时/推测强特征 => 不允许 durable memory，最多 warning 或 block。
已测试/有文件引用/用户明确要求记住 => 高分。
```

#### 2.2 可复用性 `reuse_score`，权重 25

检查项：

- 是否描述未来可重复使用的规则、偏好、流程、踩坑。
- 是否只是单次日志、短期进度、一次性命令输出。
- skill 创建/更新是否有明确触发条件与适用场景。

#### 2.3 完整性 `completeness_score`，权重 25

Memory 最低要求：

```text
事实/规则 + 适用范围/来源
```

Skill 最低要求：

```text
frontmatter name/description + Use_When/Steps 或等价结构 + Verification/Pitfalls 优先
```

用户提出的“问题/方案/原因/应用位置引用”可作为强加分项：

```text
Problem: ...
Cause: ...
Solution: ...
Applies to: file/function/tool/skill
Verified by: test/command/source
```

#### 2.4 冲突检测 `conflict_score`，权重 20

MVP 先做低成本确定性冲突：

- exact duplicate：现有 MemoryStore 已做，但 quality gate 可返回 `duplicate`。
- 关键词反向冲突：`must/never/always/use/do not` 与已有条目同主题相反。
- 同 skill 名称 create 时已存在：建议 patch/edit，而不是 create。
- memory add 内容与已有条目高度相似：建议 replace。

后续可升级为语义相似度或 LLM judge，但 MVP 不需要。

### 3. 决策策略

建议返回：

```json
{
  "quality_gate": {
    "decision": "pass|warn|block|suggest_replace|pending_review",
    "score": 84,
    "scores": {
      "stability": 28,
      "reuse": 21,
      "completeness": 20,
      "conflict": 15
    },
    "reasons": [],
    "suggestions": [],
    "expires_at": null,
    "review_after": "2026-05-21"
  }
}
```

MVP 阈值：

| Decision | 条件 | 行为 |
|---|---|---|
| `pass` | score >= 75 且无硬冲突 | 正常写入 |
| `warn` | 60-74 | 允许写入，但返回 warning，并设置较短 review_after |
| `suggest_replace` | 与已有知识相似/冲突但可定位 old_text | 阻止 add，建议 replace/patch |
| `pending_review` | 45-59 或证据不足但可能有价值 | 不写 durable memory；可写 pending index |
| `block` | <45、明显临时/推测/注入风险/硬冲突 | 拒绝写入 |

### 4. 接入 `memory_write`

位置：`mcp_serve.py:982-1025`。

流程：

```text
参数校验
  ↓
quality = evaluate_memory_write(action, target, content, old_text, existing=read_live_memory_state())
  ↓
if quality.block/suggest_replace: return structured result, 不调用 memory_write_v1
  ↓
result = memory_write_v1(...)
  ↓
if success: record_quality_metadata(...)
  ↓
return result + quality_gate
```

注意：

- 对 `replace` 操作降低冲突惩罚，因为 replace 本身就是更新旧知识。
- 对 `target=user` 用户偏好类内容，不应因“不通用”扣太多分；它的复用性是“对用户持续有效”。
- 现有 `MemoryStore` 的注入扫描仍保留，quality gate 不能替代安全扫描。

### 5. 接入 `skill_create_or_patch`

位置：`mcp_serve.py:1050-1071`，底层 `tools/skill_manager_tool.py`。

流程：

```text
create/edit/patch/write_file 前：
  evaluate_skill_change(action, name, content/patch/file_content, existing_skill_if_any)

create:
  - 检查是否已有同名/相似技能
  - 检查 frontmatter 与结构完整性
  - 检查是否有 Use_When/Steps/Verification/Pitfalls 或等价内容

patch/edit:
  - 检查是否是更新现有技能而非重复新增
  - 检查 patch 是否增加验证步骤、适用条件、踩坑或修正过期内容

write_file:
  - 对 supporting file 只做轻量检查：文件路径合法、内容是否与技能相关、是否包含明显临时/秘密/注入内容
```

建议 `delete/remove_file` 不走质量评分，只走现有安全与权限检查；但应写 audit log。

### 6. `task_context_bundle` 过期知识审核机制

位置：`mcp_serve.py:1105-1175`。

目标不是每次都重扫所有知识，而是：

```text
每次 task_context_bundle 调用时：
  1. 检查上次 audit 时间
  2. 若距今 >= 24 小时，执行 bounded audit
  3. 加载 memory/skills 时过滤或标注 stale/deprecated/pending
```

#### 6.1 审核触发

新增 profile-scoped 状态：

```json
{
  "last_audit_at": "2026-04-21T10:00:00Z",
  "audit_interval_hours": 24,
  "max_items_per_audit": 50
}
```

触发规则：

```text
now - last_audit_at >= 24h => run audit
手动参数 force_quality_audit=True 可选，MVP 可先不暴露
```

为保持兼容，`task_context_bundle` 可先不新增参数，只内置每日一次。

#### 6.2 如何判断过期知识

使用元数据优先，启发式兜底：

1. 有 `expires_at` 且已过期 => `stale`。
2. 有 `review_after` 且已超过 => `needs_review`。
3. source_type 是 external/web/api/model/version，TTL 短：7-30 天。
4. source_type 是 repo_file/code_fact，若记录了 file mtime/hash 且文件变化 => `needs_review`。
5. user explicit preference / AGENTS.md rule 默认不过期，但如果来源文件变化则复核。
6. 低分 warning 条目如果 30-90 天未使用 => `archive_candidate`。

#### 6.3 MVP 的过期判断最小实现

如果现有 memory entries 没有元数据，不能准确知道创建时间。MVP 可以：

- 只对新写入后有 index metadata 的知识做 TTL。
- 对旧 memory 条目做 `legacy_unknown` 标注，不自动淘汰。
- `task_context_bundle` 返回 `quality_audit` 字段，提示有多少条 legacy/untracked。

返回示例：

```json
"quality_audit": {
  "ran": true,
  "last_audit_at": "2026-04-21T10:00:00Z",
  "stale_count": 2,
  "legacy_untracked_count": 5,
  "notes": ["2 memory entries require review and were excluded from default memory bundle"]
}
```

#### 6.4 加载策略

建议默认：

- `active/pass/warn`：可加载。
- `needs_review`：可加载但标注；如果超期很久则降权或排除。
- `stale/deprecated/superseded/pending/block`：默认不加载。
- legacy unknown：MVP 继续加载，但返回 audit 提醒。

### 7. 元数据索引设计

`index.json` 示例：

```json
{
  "version": 1,
  "last_audit_at": null,
  "items": {
    "memory:memory:<sha256>": {
      "kind": "memory",
      "target": "memory",
      "content_hash": "...",
      "status": "active",
      "score": 86,
      "scores": {"stability": 28, "reuse": 22, "completeness": 21, "conflict": 15},
      "created_at": "2026-04-21T10:00:00Z",
      "last_verified_at": "2026-04-21T10:00:00Z",
      "review_after": "2026-05-21T10:00:00Z",
      "expires_at": null,
      "source_type": "agent_observation",
      "reasons": [],
      "supersedes": [],
      "superseded_by": null
    }
  }
}
```

技能条目 key：

```text
skill:<name>:SKILL.md:<sha256>
skill:<name>:references/foo.md:<sha256>
```

## Implementation Steps

### Step 1 — Add `tools/knowledge_quality.py`

Implement pure helper functions:

- `evaluate_memory_write(action, target, content, old_text, existing_entries) -> dict`
- `evaluate_skill_change(action, name, content, old_string, new_string, file_path, file_content, existing_skill) -> dict`
- `record_quality_metadata(kind, identity, content, gate_result) -> None`
- `audit_due_knowledge(memory_state, skills_metadata) -> dict`
- `filter_bundle_memory(entries, target) -> tuple[list, dict]`

No new dependencies.

### Step 2 — Wire quality gate into MCP `memory_write`

Modify `mcp_serve.py` around `memory_write` before `memory_write_v1(...)`.

Acceptance:

- Low-quality temporary memory is rejected or pending.
- High-quality memory still writes exactly as before.
- Response includes `quality_gate` on both pass and block.

### Step 3 — Wire quality gate into MCP `skill_create_or_patch`

Modify `mcp_serve.py` around `skill_create_or_patch` before `skill_create_or_patch_v1(...)`.

Acceptance:

- Creating a skill without frontmatter/steps/trigger conditions is blocked by quality gate before file mutation.
- Patching a skill to add verified missing steps is allowed.
- Existing security scan behavior remains unchanged.

### Step 4 — Add daily freshness audit to `task_context_bundle`

Modify `mcp_serve.py` around `task_context_bundle` after loading memory/skills and before constructing bundle.

Acceptance:

- Audit runs at most once per 24h by default.
- Bundle includes `quality_audit` summary.
- Stale/deprecated metadata-backed knowledge is excluded or marked.
- Legacy untracked entries are not automatically deleted.

### Step 5 — Tests

Add/update tests in `tests/test_mcp_serve.py` and focused unit tests if helper module is easy to import.

Test cases:

1. `memory_write` accepts stable reusable complete content.
2. `memory_write` blocks temporary/speculative content.
3. `memory_write add` suggests replace when conflict/similar existing entry detected.
4. `skill_create_or_patch create` blocks incomplete skill content.
5. `skill_create_or_patch patch` permits targeted update.
6. `task_context_bundle` includes `quality_audit` and does not run audit again within 24h.
7. Stale metadata-backed memory is filtered or annotated.
8. Existing parameter validation errors still behave as before.

Use `scripts/run_tests.sh`, not direct pytest, per repo policy.

## Acceptance Criteria

- [ ] MCP `memory_write` has quality gate before durable writes.
- [ ] MCP `skill_create_or_patch` has quality gate before create/edit/patch/write_file writes.
- [ ] Quality gate returns stable machine-readable metadata: `decision`, `score`, `scores`, `reasons`, `suggestions`, `review_after`/`expires_at`.
- [ ] Existing successful writes remain backward compatible with additive response fields only.
- [ ] `task_context_bundle` runs bounded freshness audit no more than once per day by default.
- [ ] Stale/deprecated/pending knowledge does not silently enter default task context.
- [ ] Legacy untracked knowledge is not destructively deleted.
- [ ] Tests cover pass/warn/block/suggest_replace and daily audit throttling.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| 评分过严阻止有用知识 | MVP 使用 `warn`/`pending_review`，只有明显临时/冲突/危险内容 block。 |
| 评分过松仍污染记忆 | 对临时/推测/冲突设置硬规则；后续可升级语义检测。 |
| 破坏 MCP 调用兼容 | 不改工具名和必填参数；只增加返回字段。 |
| 旧知识无元数据无法判断过期 | 标注 `legacy_untracked`，不自动淘汰；从新写入开始可审计。 |
| `task_context_bundle` 每次变慢 | 每 24h 执行 bounded audit；平时只读 index。 |
| 文件锁/并发问题 | 复用现有 memory 文件锁；quality index 写入用临时文件 + atomic replace。 |

## Verification Steps

1. 运行单测：

```bash
scripts/run_tests.sh tests/test_mcp_serve.py -v --tb=long
```

2. 如果新增独立测试文件：

```bash
scripts/run_tests.sh tests/test_knowledge_quality.py tests/test_mcp_serve.py -v --tb=long
```

3. 最终运行相关套件或全量：

```bash
scripts/run_tests.sh
```

## ADR

### Decision

在现有 MCP `memory_write` 与 `skill_create_or_patch` 写入路径中增加知识质量评分门禁，并在 `task_context_bundle` 中增加每日过期/淘汰审计。

### Drivers

- 写入口集中，低侵入。
- 用户希望“在现有流程里增加知识审核”。
- 长期知识污染影响跨会话、多 agent 使用，必须前置治理。

### Alternatives considered

1. 独立 `knowledge_validate/commit` 工具：架构干净，但调用方更容易绕过。
2. 只做后台审计：实现简单，但污染已经进入 durable memory。
3. 使用 LLM judge 做评分：效果可能更细，但引入成本、不稳定性和依赖。

### Why chosen

现阶段最优是“嵌入现有写入工具 + task_context_bundle 轻审计”：最快形成闭环，兼容现有 MCP 使用方式，收益最大。

### Consequences

- 写入响应会多出 `quality_gate` 字段。
- 新写入知识会有 profile-scoped 元数据索引。
- 旧知识不会立即被淘汰，只会被标注为 legacy/untracked。

### Follow-ups

- 后续可新增显式 MCP 工具：`knowledge_quality_status`、`knowledge_revalidate`、`knowledge_prune`。
- 后续可加入语义相似度/冲突检测。
- 后续可把评分摘要展示到 CLI/TUI 或 MCP bundle hints。

## Follow-up Staffing Guidance

### `$ralph` path

适合一个 owner 持续实现与验证：

- `executor`：实现 `tools/knowledge_quality.py` 与 MCP 接入。
- `test-engineer`：补充质量门禁和审计测试。
- `verifier`：确认兼容性和测试证据。

### `$team` path

如果并行执行，可拆为：

1. Memory gate lane：`mcp_serve.py` + `tools/knowledge_quality.py` memory 部分。
2. Skill gate lane：`mcp_serve.py` + `tools/skill_manager_tool.py` 相关测试。
3. Bundle audit lane：`task_context_bundle` 过滤/审计 + 测试。
4. Verification lane：运行测试、检查返回结构兼容。

Launch hint:

```text
$team implement .omx/plans/plan-knowledge-quality-gate-mcp.md
```

Team verification path:

- Team 需证明：单测通过、MCP 返回结构兼容、低质量写入被拦截、过期审计每日限频。
- Ralph 后续复核：是否有遗漏写入路径绕过 gate；是否需要为 agent-loop memory tool 同步同一 gate。
