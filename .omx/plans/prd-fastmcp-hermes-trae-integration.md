# PRD — FastMCP Hermes/Trae Integration

## Metadata
- Source spec: `.omx/specs/deep-interview-fastmcp-hermes-trae-integration.md`
- Planning mode: `$ralplan` consensus, short mode
- Status: approved-for-execution best version after planner/architect/critic review loop

## Problem Statement
Trae is the active LLM orchestrator, but Hermes already holds durable assets that Trae lacks as first-class MCP tools: long-term memory, cross-session recall, and evolving skills. The current Hermes FastMCP server in `mcp_serve.py` only exposes messaging bridge tools. We need a first-phase MCP surface that lets Trae retrieve relevant Hermes context before coding and write back durable learnings after coding, without embedding a Hermes-owned LLM runtime or turning Hermes into a second autonomous agent.

## Desired Outcome
Expose a deterministic, tool-first MCP surface so Trae can:
1. retrieve relevant built-in memory, deterministic recall evidence, and skill candidates before a task,
2. write durable long-term memory after a task,
3. create or patch reusable skills from successful solutions.

## Non-goals
- No multi-tenant support.
- No remote/hosted service deployment requirement.
- No embedded Hermes LLM path for this feature.
- No Hermes-owned autonomous agent/runtime.
- No GUI / Trae plugin UI work.
- No provider-aware memory exposure in v1.

## Principles
1. Tool-first deterministic integration.
2. MCP-native safe adapters over brownfield internals.
3. Metadata-first retrieval and progressive disclosure.
4. Narrower v1 mutation scope than internal tool capability.
5. Preserve existing messaging MCP tools while expanding discoverability.

## Decision Drivers
1. Must obey no-LLM/no-second-runtime architecture constraints.
2. Must be usable enough for Trae to reduce roundtrips for pre-task context.
3. Must avoid exposing hidden side effects from internal Hermes tools.

## Options Considered
### Option A — Atomic-only adapters
Expose only atomic deterministic MCP tools.
- Pros: cleanest API, least coupling.
- Cons: more roundtrips and more client orchestration burden.

### Option B — Hybrid contract
Expose atomic adapters as the primary contract plus one bounded convenience bundle.
- Pros: best usability/safety tradeoff.
- Cons: more contract design work.

### Option C — Direct export of internal tools
Expose existing Hermes tools broadly through MCP.
- Pros: fastest apparent implementation.
- Cons: leaks side effects, broadens scope, conflicts with v1 contract discipline.

## Decision
Choose **Option B**.
Atomic adapters are the primary contract. `task_context_bundle` ships in v1 as a bounded convenience index only.

## Frozen v1 MCP Contract
The following 7 tools are added to the existing MCP server surface.

### 1. `memory_read`
Purpose: retrieve live built-in memory from the active profile.

**Scope**
- Only built-in `MEMORY.md` and `USER.md` under active `HERMES_HOME`.
- No prompt snapshot semantics.

**Implementation rule**
- Do not call the agent-level `memory_tool()` dispatcher.
- Add a dedicated helper in `tools/memory_tool.py` that instantiates/loads a fresh `MemoryStore` or equivalent disk-read helper on each MCP call.

**Input schema**
- no required arguments

**Output schema**
- `memory: string[]`
- `user: string[]`
- `memory_count: int`
- `user_count: int`

### 2. `memory_write`
Purpose: write durable built-in memory.

**Scope**
- Built-in local memory only.

**Allowed actions in v1**
- `add`
- `replace`

**Forbidden in v1**
- `remove`

**Input schema**
- `action: "add" | "replace"`
- `target: "memory" | "user"` (required)
- `content: string` (required)
- `old_text: string` (required only when `action="replace"`)

**Output schema**
- `success: bool`
- `target: "memory" | "user"`
- `action: "add" | "replace"`
- `message: string`
- `entries: string[]`

**Negative contract**
- `memory_write(remove)` must fail with a structured error.

### 3. `session_recall_search`
Purpose: deterministic cross-session evidence retrieval.

**Implementation rule**
- Use `SessionDB.search_messages()` directly or a thin deterministic helper over it.
- Must not call auxiliary summarization or any Hermes-owned LLM path.

**Input schema**
- `query: string` (required)
- `limit: int` (optional, default 5, hard cap 10)

**Output schema**
- `results: object[]`
  - each result includes:
    - `session_id: string`
    - `source: string`
    - `timestamp: string | number | null`
    - `message_id: string | number | null`
    - `snippet: string` (max 300 chars)
    - optional `context_before: string` (<=150 chars)
    - optional `context_after: string` (<=150 chars)

**Selection rule**
- Top ranked deterministic FTS5 hits in DB/rank order.

### 4. `skills_list`
Purpose: deterministic metadata listing for v1-safe skills.

**Scope**
- Profile-local skills only under active `~/.hermes/skills`.
- Exclude configured external dirs and plugin-qualified skills in v1.

**Implementation rule**
- If existing internal `skills_list()` cannot be safely narrowed, add a dedicated local-only metadata helper.

**Input schema**
- `query: string` (optional)
- `limit: int` (optional, default 5, hard cap 20)

**Output schema**
- `skills: object[]`
  - `name`
  - `description`
  - optional `category`
  - optional `tags`

**Selection rule**
- When query is present, rank by deterministic name/description/category/tag match.

### 5. `skill_view_safe`
Purpose: safe profile-local skill content retrieval.

**Scope**
- Only active-profile `~/.hermes/skills`.
- Exclude plugin-qualified names, external dirs, env/setup capture, passthrough registration, and other side effects.

**Implementation rule**
- Do not call existing `skill_view()`.
- Implement a new local-only helper directly over profile `SKILLS_DIR`.

**Allowed linked-file categories in v1**
- `references/`
- `templates/`
- `assets/`

**Excluded in v1**
- `scripts/`

**Input schema**
- `name: string` (required)
- `file_path: string` (optional, only allowed under the categories above)

**Output schema**
- `name: string`
- `content: string`
- `file_path?: string`
- `linked_files?: string[]`

**Negative contract**
- Plugin-qualified names and out-of-profile sources must fail.

### 6. `skill_create_or_patch`
Purpose: narrow v1 skill mutation adapter.

**Allowed actions in v1**
- `create`
- `patch`

**Forbidden in v1**
- `edit`
- `delete`
- `write_file`
- `remove_file`

**Patch mutation scope in v1**
- `SKILL.md` only.
- No supporting-file mutation in v1.
- No `scripts/` mutation in v1.

**Input schema**
- `action: "create" | "patch"`
- `name: string` (required)
- `category: string` (optional, allowed only when `action="create"`)
- `content: string` (required when `action="create"`)
- `old_string: string` (required when `action="patch"`)
- `new_string: string` (required when `action="patch"`)
- `replace_all: bool` (optional, default `false`, only for patch)

**Output schema**
- `success: bool`
- `action: "create" | "patch"`
- `name: string`
- `message: string`

**Negative contract**
- Forbidden actions must fail with structured error.

### 7. `task_context_bundle`
Purpose: bounded convenience index for pre-task context.

**Role**
- Secondary to atomic adapters.
- Must not return full skill bodies by default.

**Input schema**
- `query: string` (required)
- `memory_limit: int` (optional, default 5, hard cap 5)
- `session_limit: int` (optional, default 5, hard cap 5)
- `skill_limit: int` (optional, default 5, hard cap 5)

**Composition rules**
- `memory` = last N built-in memory entries from `memory_read`
- `user` = last N user-memory entries from `memory_read`
- `session_hits` = top `session_recall_search(query)` results
- `skill_candidates` = top profile-local metadata matches from `skills_list(query)`
- `hints` = fixed template strings only

**Output schema**
- `memory: string[]`
- `user: string[]`
- `session_hits: object[]`
- `skill_candidates: object[]`
- `hints: string[]`

**Hard bounds**
- max 5 `memory`
- max 5 `user`
- max 5 `session_hits`
- max 5 `skill_candidates`
- no full skill bodies by default

## Phases
### Phase 0 — Contract freeze and server identity update
- Freeze the 7 new MCP tool names and schemas.
- Update `mcp_serve.py` instructions/identity to advertise both messaging and learning surfaces.
- Update registration tests to expect existing messaging tools + these 7 tools.
- Add acceptance point that server instructions mention messaging and memory/skills/recall surfaces.

### Phase 1 — Deterministic retrieval adapters
- Add live disk-based helper for `memory_read` in `tools/memory_tool.py`.
- Add deterministic recall helper in `hermes_state.py` or `tools/session_search_tool.py` without any LLM path.
- Add profile-local metadata helper for MCP `skills_list` if needed.
- Add `skill_view_safe` helper with profile-local-only scope and no side effects.
- Register `memory_read`, `session_recall_search`, `skills_list`, `skill_view_safe`, and `task_context_bundle` in `mcp_serve.py`.

### Phase 2 — Narrow mutation adapters
- Register `memory_write` with only `add|replace`.
- Register `skill_create_or_patch` with only `create|patch` and SKILL.md-only patch scope.
- Reuse underlying validators/scans/atomic-write logic.

### Phase 3 — Hardening and regressions
- Extend `tests/test_mcp_serve.py` for exact tool-name registration and server-identity assertions.
- Add negative tests for forbidden mutation actions and side-effect-free retrieval.
- Regression-test existing messaging MCP tools unchanged.
- Update docs/help text.

## Touched Files / Modules
- `mcp_serve.py`
- `tools/memory_tool.py`
- `hermes_state.py` and/or `tools/session_search_tool.py`
- `tools/skills_tool.py`
- `tools/skill_manager_tool.py`
- `tests/test_mcp_serve.py`
- `tests/tools/test_memory_tool.py`
- `tests/tools/test_session_search.py`
- `tests/tools/test_skills_tool.py`
- `tests/tools/test_skill_manager_tool.py`

## Risks and Mitigations
| Risk | Mitigation |
|---|---|
| Hidden LLM path in recall | Dedicated deterministic helper + monkeypatched failure tests |
| Side-effectful skill reads | New `skill_view_safe` helper; do not call `skill_view()` |
| Mutation blast radius | Narrow v1 actions and SKILL.md-only patch scope |
| Bundle over-coupling | Keep bundle bounded, metadata-first, and secondary |
| Contract confusion | Freeze MCP-native names/schemas and update server identity/tests |

## Acceptance Criteria
1. `memory_read` returns live built-in memory/user entries from active profile.
2. `memory_write(add|replace)` persists correctly; `memory_write(remove)` fails.
3. `session_recall_search(query)` returns bounded deterministic evidence with no Hermes-owned LLM path.
4. MCP `skills_list` returns profile-local metadata-only skill candidates.
5. `skill_view_safe` returns profile-local skill content safely and rejects plugin-qualified/external sources.
6. `skill_create_or_patch(create|patch)` works on frozen input schema; forbidden actions fail.
7. `task_context_bundle(query)` returns bounded references/snippets only and no full skill-body dump by default.
8. Existing messaging MCP tools continue to work unchanged.
9. Updated FastMCP server instructions clearly describe both messaging and learning surfaces.

## Verification
Use `scripts/run_tests.sh`.

Required suites:
- `scripts/run_tests.sh tests/test_mcp_serve.py`
- `scripts/run_tests.sh tests/tools/test_memory_tool.py`
- `scripts/run_tests.sh tests/tools/test_session_search.py`
- `scripts/run_tests.sh tests/tools/test_skills_tool.py`
- `scripts/run_tests.sh tests/tools/test_skill_manager_tool.py`

Add targeted tests for:
- exact FastMCP registration set = existing messaging tools + the 7 new tools
- server instruction string mentions messaging and learning surfaces
- summarizer monkeypatched to fail while `session_recall_search` and `task_context_bundle` still pass
- `skill_view_safe` local-only behavior and forbidden source rejection
- `memory_write(remove)` fails
- `skill_create_or_patch(edit|delete|write_file|remove_file)` fails
- patch is limited to `SKILL.md`

## ADR
### Decision
Implement a hybrid MCP surface using MCP-native safe adapters as the primary contract, with `task_context_bundle(query)` in v1 as a bounded convenience index.

### Drivers
- Architecture constraints
- Trae usability
- Safe brownfield reuse

### Alternatives considered
- Atomic-only adapters
- Raw internal-tool export
- Autonomous Hermes runtime

### Why chosen
Balances ergonomics with contract safety and avoids hidden side effects.

### Consequences
- Some extra adapter code now
- Lower long-term API leakage/coupling later

### Follow-ups
- Provider-aware memory exposure
- Audit/logging semantics for writes
- Extraction of non-messaging MCP registration helpers

## Available agent roster for execution
- `architect`
- `executor`
- `critic`
- `test-engineer`
- `verifier`
- `debugger`

## Staffing guidance
### Recommended path
Prefer **`$ralph`** for sequential implementation because `mcp_serve.py` is a hotspot and the no-hidden-LLM constraint benefits from tight ownership.

### If using `$team`
Split lanes by write scope:
1. `mcp_serve.py` contract/integration
2. deterministic recall helper
3. memory adapters
4. skill adapters
5. verification/regression

### Suggested reasoning intensity
- high: contract/integration, deterministic recall
- medium-high: memory adapters, skill adapters
- medium: verification/regression
