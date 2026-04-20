# Deep Interview Spec — FastMCP Hermes/Trae Integration

## Metadata
- Profile: standard
- Rounds: 6
- Final ambiguity: 0.18
- Threshold: 0.20
- Context type: brownfield
- Context snapshot: `.omx/context/fastmcp-hermes-trae-integration-20260420T052725Z.md`
- Transcript summary: `.omx/interviews/fastmcp-hermes-trae-integration-fastmcp-hermes-trae-integration-20260420T054653Z.md`

## Clarity Breakdown
| Dimension | Score |
|---|---:|
| Intent Clarity | 0.90 |
| Outcome Clarity | 0.82 |
| Scope Clarity | 0.88 |
| Constraint Clarity | 0.88 |
| Success Criteria Clarity | 0.82 |
| Context Clarity | 0.82 |

## Intent
User mainly codes inside Trae and wants Hermes to act as a persistent learning substrate behind Trae. The core purpose is to let Trae use Hermes memory and skills before coding for task orchestration/correction, then push completed solutions back into Hermes as long-term memory and evolving skills so future Trae sessions improve.

## Desired Outcome
Expose Hermes long-memory and skill-evolution capabilities through FastMCP so external MCP clients such as Trae can:
1. retrieve relevant memory and skills before doing a coding task,
2. write back durable memory after task completion,
3. create or patch reusable skills from successful solutions.

## In Scope
- Extend Hermes's existing FastMCP server surface beyond messaging bridge tools.
- Expose Hermes's core memory and skill capabilities as MCP-callable tools.
- Support direct read/write flows initiated by Trae's model.
- Likely first-phase surfaces include:
  - pre-task retrieval of relevant memory + skills
  - post-task writeback to long-term memory
  - solution-to-skill creation or patching
- Tool design may include both:
  - low-level atomic tools (memory/skill/session primitives)
  - high-level aggregation tools that package common workflows, as long as they remain tool-shaped and do not require Hermes-owned LLM orchestration.

## Out of Scope / Non-goals
- No multi-tenant support.
- No remote-service / hosted deployment as a first-phase requirement.
- No embedded LLM inside Hermes for this integration.
- No Hermes self-reasoning runtime or second autonomous agent runtime.
- No GUI or Trae-plugin-side UI work.

## Decision Boundaries
Hermes may decide without further confirmation:
- how to map existing Hermes memory/skill internals into MCP tool shapes,
- whether first-phase surfaces are split into atomic tools, aggregation tools, or both,
- naming and schema design of those tools,
- which existing Hermes components are wrapped first.

Hermes should not assume without confirmation in a downstream planning/execution phase:
- whether to expose only built-in memory vs also external memory providers,
- exact mutation safeguards, validation, and rollback semantics for skill/memory writes,
- whether high-level aggregation tools should be deterministic-only or allowed to invoke Hermes internal agent loops in the future.

## Constraints
- Trae's own LLM is the orchestrator; Hermes MCP only provides tools.
- Therefore a fully autonomous 'Hermes-owned' closed-loop workflow is not the first-phase architecture.
- First phase allows direct write access from Trae into Hermes internal assets.
- The design should fit the current Hermes brownfield structure:
  - `mcp_serve.py` currently exposes messaging bridge tools only
  - `tools/memory_tool.py` handles built-in MEMORY.md/USER.md
  - `tools/session_search_tool.py` handles cross-session recall
  - `tools/skills_tool.py` exposes `skills_list` / `skill_view`
  - `tools/skill_manager_tool.py` exposes `skill_manage`

## Testable Acceptance Criteria
A first-phase implementation is successful if Trae can reliably do all of the following through FastMCP:
1. Before starting a coding task, retrieve a relevant package of Hermes memory and skill context.
2. After completing a task, write durable long-term memory back into Hermes.
3. Convert a successful solution into either a new skill or a patch to an existing skill.
4. All of the above work without introducing an embedded Hermes LLM runtime or second autonomous agent loop.

## Assumptions Exposed + Resolutions
- **Assumption:** To get a useful learning loop, Hermes might need to own high-level orchestration.
  - **Resolution:** Not in first phase. Since Trae's model does orchestration and Hermes is tool-only over MCP, the system should be tool-first.
- **Assumption:** A closed-loop integration might need to start read-only for safety.
  - **Resolution:** Rejected by the user. First phase should allow direct writes to Hermes internal assets.

## Pressure-pass Findings
The main pressure-pass result was architectural: once the user clarified that Hermes does not own the active LLM in this integration, the recommended design shifted from a full autonomous loop to a tool-first MCP surface that still preserves learning/writeback.

## Brownfield Evidence vs Inference Notes
### Evidence from repository
- `mcp_serve.py` already defines a FastMCP server, but current tools are messaging/event bridge focused.
- `run_agent.py` and `agent/memory_manager.py` show a learning loop with prefetch, sync, queue_prefetch, and periodic background nudges.
- `tools/session_search_tool.py` provides long-term recall over past sessions.
- `tools/skills_tool.py` and `tools/skill_manager_tool.py` provide read/write skill primitives.

### Inference from evidence
- A first-phase Trae integration can likely be implemented by wrapping or adapting existing Hermes primitives behind new MCP tools rather than building a new learning engine from scratch.
- 'High-level workflow tools' are still feasible if they are deterministic wrappers/aggregators over existing primitives rather than autonomous Hermes-owned reasoning loops.

## Technical Context Findings
- Existing FastMCP entrypoint: `mcp_serve.py`
- Existing memory internals: built-in memory tool + pluggable memory providers
- Existing skill internals: listing, viewing, creating, patching, supporting files
- Existing recall primitive: session_search summaries over session DB

## Recommended Handoff
Recommended next step: **`$ralplan`** to decide the MCP surface contract and first-phase tool taxonomy.

Suggested planning focus:
1. first-phase tool inventory (atomic vs aggregated),
2. memory scope (built-in only vs provider-aware),
3. mutation semantics for skill/memory writes,
4. verification/tests for FastMCP exposure.
