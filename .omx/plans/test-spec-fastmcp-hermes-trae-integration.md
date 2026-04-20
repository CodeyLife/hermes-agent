# Test Spec — FastMCP Hermes/Trae Integration

## Scope
Validate the new learning-oriented MCP surface added alongside the existing messaging MCP tools.

## Required New Tools
- `memory_read`
- `memory_write`
- `session_recall_search`
- `skills_list`
- `skill_view_safe`
- `skill_create_or_patch`
- `task_context_bundle`

## Core Assertions
1. Registration includes existing messaging tools plus the 7 new tools.
2. Server instructions mention both messaging and learning surfaces.
3. Retrieval path is deterministic and does not invoke auxiliary summarization.
4. Skill reads are profile-local and side-effect free.
5. Mutation scope is narrowed exactly as defined in the PRD.
6. Existing messaging MCP tools remain unchanged.

## Test Matrix
### A. MCP registration and identity
- Assert exact FastMCP tool-name set.
- Assert server instruction string mentions messaging plus memory/skills/recall.

### B. memory_read
- Reads live MEMORY.md / USER.md from active profile.
- Returns counts and arrays.
- Does not depend on an active agent/store snapshot.

### C. memory_write
- `add` works for `memory` and `user`.
- `replace` works with `old_text + content`.
- `remove` is rejected.
- Validation/injection scan behavior preserved.

### D. session_recall_search
- Uses deterministic search path.
- `query` required.
- Default result count is 5, capped at 10.
- Snippets bounded to 300 chars.
- If context fields present, each bounded to 150 chars.
- Auxiliary summarization path monkeypatched to fail should not affect success.

### E. skills_list
- Returns profile-local metadata only.
- Excludes plugin-qualified or external-dir results in v1.
- Query ranking behaves deterministically.

### F. skill_view_safe
- Reads only from active-profile `~/.hermes/skills`.
- Does not call or trigger `skill_view()` side effects.
- Allows `references/`, `templates/`, `assets/`.
- Rejects `scripts/`, plugin-qualified names, and out-of-profile sources.

### G. skill_create_or_patch
- `create` works with `name`, optional `category`, and full `content`.
- `patch` works only on `SKILL.md` with `old_string`, `new_string`, optional `replace_all`.
- `edit`, `delete`, `write_file`, `remove_file` are rejected.
- Supporting-file mutation is rejected in v1.
- Security scan / atomic-write protections remain enforced.

### H. task_context_bundle
- Requires `query`.
- Returns bounded arrays only:
  - max 5 memory
  - max 5 user
  - max 5 session_hits
  - max 5 skill_candidates
- Does not inline full skill bodies by default.
- Hints are fixed template strings only.
- Uses top deterministic session hits and top local-only skill metadata matches.
- Monkeypatched summarizer failure does not break bundle.

### I. Regression
- Existing messaging MCP tools still register and behave as before.

## Suggested Test Files
- `tests/test_mcp_serve.py`
- `tests/tools/test_memory_tool.py`
- `tests/tools/test_session_search.py`
- `tests/tools/test_skills_tool.py`
- `tests/tools/test_skill_manager_tool.py`

## Execution Commands
```bash
scripts/run_tests.sh tests/test_mcp_serve.py
scripts/run_tests.sh tests/tools/test_memory_tool.py
scripts/run_tests.sh tests/tools/test_session_search.py
scripts/run_tests.sh tests/tools/test_skills_tool.py
scripts/run_tests.sh tests/tools/test_skill_manager_tool.py
```

## Release Gate
The feature is ready for execution completion when:
- all required tests pass,
- deterministic no-LLM recall is proven,
- forbidden mutation actions are proven to fail,
- existing messaging MCP tools are proven unchanged.
