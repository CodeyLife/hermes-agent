# Plan: MCP Session Persistence for `task_context_bundle` Session Recall

## Requirements Summary

`task_context_bundle(...)` currently includes `session_hits` by calling deterministic session recall, but MCP clients that only use Hermes through `hermes mcp serve` have no write path that records their own turns into `SessionDB`. This makes Session Recall look supported while returning empty or unrelated Hermes CLI/gateway history for MCP-only workflows.

Grounded observations:

- `mcp_serve.py:253-314` implements `_deterministic_session_recall_search(...)` by calling `db.search_messages(...)` and returning snippets/context only.
- `mcp_serve.py:1209-1289` implements `task_context_bundle(...)`; it always queries `_get_session_db()` and includes `session_hits` in the returned bundle.
- `mcp_serve.py:620-760` exposes read-only conversation tools over gateway `sessions.json` + `SessionDB`, but no MCP-native `session_*` write tools.
- `mcp_serve.py:981-1055` exposes `memory_write(...)`; this is for durable declarative memory, not per-turn transcript/session data.
- `hermes_state.py:791-850` already has `SessionDB.append_message(...)` with FTS triggers, and `hermes_state.py:1006-1146` has `search_messages(...)`, including CJK LIKE fallback and surrounding context.
- Native Hermes flows already persist sessions: `run_agent.py:1277-1294` creates a session when an agent has a `session_db`, and `run_agent.py:2720-2765` flushes messages to it; CLI branch/resume/reset also uses `SessionDB` (`cli.py:4314-4326`, `cli.py:4455-4484`); gateway persists transcripts via `gateway/session.py:1104-1156`.

Therefore the gap is not the storage engine; it is the MCP-facing ingestion API and lifecycle contract for external clients.

## Acceptance Criteria

1. MCP clients can create or resume a Hermes-backed local session without needing gateway `sessions.json`.
2. MCP clients can append user/assistant/tool messages to that session using a bounded, schema-validated MCP tool.
3. `session_recall_search(query=...)` and `task_context_bundle(query=...)` can retrieve MCP-written messages through the existing `SessionDB.search_messages(...)` path.
4. MCP session storage uses `get_hermes_home()` / `SessionDB()` so profiles remain isolated.
5. MCP session writes do not use `memory_write(...)`; durable memory remains declarative and curated.
6. Session recall should not fail the entire `task_context_bundle` when the DB is absent or empty; empty recall should be a successful empty section, while DB errors should be explicit but non-fatal where possible.
7. Tests cover create/append/search/bundle behavior and profile/path isolation assumptions.

## Recommended Design

Add an MCP-native, explicit transcript ingestion surface backed by existing `SessionDB`:

- `session_start(...)` or `mcp_session_start(...)`
  - Inputs: optional `session_id`, optional `title`, optional `cwd`, optional `client_name`, optional `metadata`.
  - Behavior: create or ensure a `SessionDB` session with `source="mcp"` or `source=f"mcp:{client_name}"` after sanitizing `client_name`.
  - Return: `session_id`, `source`, `created/resumed`, `message_count`.

- `session_append(...)` or `mcp_session_append(...)`
  - Inputs: `session_id`, `role`, `content`, optional `tool_name`, `tool_calls`, `tool_call_id`, optional `timestamp` only if the DB API later supports it.
  - Allowed roles: at minimum `user`, `assistant`, `tool`, `system`; consider disallowing `system` by default unless needed.
  - Behavior: call `SessionDB.ensure_session(...)` then `append_message(...)`; enforce content length limits and JSON-serializable structured fields.
  - Return: `message_id`, `session_id`, `indexed=true`.

- `session_get(...)` / reuse `messages_read(...)` with direct `session_id`
  - Current `messages_read(...)` requires a gateway `session_key` from `sessions.json` (`mcp_serve.py:710-729`). MCP-created sessions will not have such a key. Add direct `session_id` read support or a new MCP session reader.

- Optional `session_end(session_id, reason="mcp_client_end")`
  - Calls `SessionDB.end_session(...)`; useful for lifecycle hygiene but not required for search.

Keep `session_recall_search(...)` as the shared retrieval endpoint. Do not create a separate recall database unless `SessionDB` becomes insufficient.

## Viable Options

### Option A — Explicit MCP session tools backed by `SessionDB` (recommended)

**Approach:** Add new MCP tools that external MCP clients call to persist session transcripts into the existing SQLite session store.

**Pros:**
- Reuses existing FTS5 search and CJK fallback in `hermes_state.py:1006-1146`.
- Aligns with CLI/gateway/ACP storage instead of inventing a second memory plane.
- Keeps `memory_write(...)` semantics clean: memory is curated facts, sessions are transcripts.
- Easy to test in `tests/test_mcp_serve.py` using existing MCP tool harness.

**Cons:**
- Requires clients/Trae rules to call append tools at turn boundaries.
- Needs idempotency guidance to avoid duplicated appends if a client retries.

### Option B — Store summaries in `memory_write(...)`

**Approach:** Treat each session/turn summary as a memory entry.

**Pros:**
- Existing MCP write tool already exists.
- No schema migration.

**Cons:**
- Violates prompt guidance in `agent/prompt_builder.py:150-155` that task progress/session outcomes should not be stored in memory.
- Pollutes durable user/project memory with temporary transcripts.
- Cannot reconstruct turn context and weakens `session_recall_search` semantics.

### Option C — MCP resource-only/session file log outside `SessionDB`

**Approach:** Persist MCP transcripts as JSONL under `HERMES_HOME/mcp_sessions/` and have recall search both SQLite and JSONL.

**Pros:**
- Isolated from existing `SessionDB` schema.
- Can support arbitrary external-client metadata.

**Cons:**
- Duplicates storage and indexing logic.
- Requires a second search/index implementation or periodic import.
- Increases maintenance burden and can produce inconsistent recall results.

## Implementation Steps

1. **Define MCP session write contract in `mcp_serve.py`.**
   - Add constants near existing MCP limits: `MCP_SESSION_CONTENT_MAX_LENGTH`, `MCP_SESSION_METADATA_MAX_LENGTH`.
   - Add helper `_normalize_mcp_session_source(client_name: Optional[str]) -> str`, bounded to safe characters and length.
   - Add helper `_get_or_error_session_db()` if useful, preserving existing `_get_session_db()` behavior.

2. **Add `session_start` tool in `mcp_serve.py`.**
   - Use `_get_session_db()` and `SessionDB.create_session(...)` or `ensure_session(...)`.
   - If no `session_id` provided, generate stable Hermes-style ID: timestamp + short UUID.
   - Store `source="mcp"` or `mcp:<client>`; pass `model_config` metadata only after bounding JSON size.
   - Optional title support can use existing `set_session_title(...)` if present.

3. **Add `session_append` tool in `mcp_serve.py`.**
   - Validate `session_id`, `role`, `content`; reject content over the limit.
   - Call `ensure_session(session_id, source="mcp")` before appending to tolerate clients that skipped start.
   - Append through `SessionDB.append_message(...)` so FTS triggers index the content.
   - Return the DB row ID.

4. **Add direct session read support.**
   - Either add `session_messages_read(session_id, limit=50)` or extend `messages_read(...)` with an optional `session_id` parameter.
   - Prefer a new tool to avoid changing the gateway `session_key` contract.

5. **Make `task_context_bundle` degrade gracefully for empty/unavailable recall.**
   - If `_deterministic_session_recall_search(...)` returns unavailable DB, include `session_hits: []` plus `session_recall_status` instead of failing the whole bundle.
   - Keep hard failure for malformed input (`query` missing).

6. **Update rules/docs that instruct MCP clients.**
   - `配置参考.md:90-91` and `tools/trae_rules_tool.py:45-46` currently tell clients to read `task_context_bundle` / `session_recall_search` but not to store sessions.
   - Add a short lifecycle rule:
     1. At task start, call `session_start(client_name="trae", cwd=...)` and keep `session_id`.
     2. After each user/assistant/tool turn, call `session_append(...)`.
     3. Before complex tasks, call `task_context_bundle(...)`.
     4. At completion, optionally call `session_end(...)`.

7. **Add tests.**
   - `tests/test_mcp_serve.py`: new test that starts an MCP session, appends a distinctive CJK/English message, then verifies `session_recall_search` finds it.
   - New test that `task_context_bundle` includes MCP-written `session_hits`.
   - New test that `task_context_bundle` still succeeds with `session_hits=[]` when session DB is unavailable.
   - New test that invalid roles / oversize content are rejected.

8. **Run verification.**
   - Targeted: `scripts/run_tests.sh tests/test_mcp_serve.py tests/test_hermes_state.py -q`
   - If touched docs/tool rules only: still run `scripts/run_tests.sh tests/test_mcp_serve.py -q`.
   - Before merge: full `scripts/run_tests.sh` per project policy.

## Risks and Mitigations

- **Duplicate message writes from MCP retries.**
  - Mitigation: add optional `client_message_id`; either store in metadata or later add a uniqueness layer. For first version, document best-effort and make tool response include `message_id`.

- **Untrusted MCP clients writing excessive or sensitive transcript data.**
  - Mitigation: enforce content limits; keep storage local to `HERMES_HOME`; do not auto-promote to memory.

- **Source-filter confusion between native Hermes sessions and MCP sessions.**
  - Mitigation: use `source="mcp"` or `source="mcp:<client>"`; consider adding `source_filter` to `session_recall_search` later, but keep default cross-source recall.

- **Current tests use doubles that only implement `search_messages`.**
  - Mitigation: add tools in a way that existing recall tests remain compatible; create dedicated session-write test doubles or use real `SessionDB`.

## Verification Steps

- Confirm `session_append` inserts rows through `SessionDB.append_message(...)` and therefore triggers FTS indexing.
- Confirm `session_recall_search` returns MCP-written snippets without invoking LLM summarization.
- Confirm `task_context_bundle` returns memory/user/skills even if session recall has zero hits.
- Confirm profile isolation by setting `HERMES_HOME` in test and verifying DB path stays under temp profile home.

## ADR

**Decision:** Add explicit MCP-native session lifecycle/write tools backed by the existing `SessionDB`.

**Drivers:**
1. `task_context_bundle` already reads from `SessionDB`; storage should feed the same index.
2. Session transcripts and durable memories have different semantics and should not be mixed.
3. Existing Hermes CLI/gateway/ACP flows already depend on `SessionDB`, so reuse minimizes new infrastructure.

**Alternatives considered:**
- Use `memory_write` for session summaries — rejected because it pollutes durable memory and conflicts with existing prompt guidance.
- Store MCP sessions in separate JSONL/resource files — rejected because it duplicates indexing/search and splits recall behavior.

**Why chosen:** Existing `SessionDB` already provides session lifecycle, message storage, FTS5, CJK fallback, and recall query behavior. The missing piece is only a safe MCP ingestion API.

**Consequences:** MCP clients must adopt a simple lifecycle protocol. Hermes gains consistent cross-session recall for MCP-only usage without changing the recall engine.

**Follow-ups:** Add optional `client_message_id` idempotency and `source_filter` support if duplicate writes or cross-client noise become real issues.
