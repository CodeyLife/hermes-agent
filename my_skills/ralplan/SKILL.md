---
name: ralplan
description: Alias for $plan --consensus
---

# Ralplan (Consensus Planning Alias)

Ralplan is a shorthand alias for `$plan --consensus`. It triggers iterative planning with Planner, Architect, and Critic agents until consensus is reached, with **RALPLAN-DR structured deliberation** (short mode by default, deliberate mode for high-risk work).

## Usage

```
$ralplan "task description"
```

## Flags

- `--interactive`: Enables user prompts at key decision points (draft review in step 2 and final approval in step 6). Without this flag the workflow runs fully automated — Planner → Architect → Critic loop — and outputs the final plan without asking for confirmation.
- `--deliberate`: Forces deliberate mode for high-risk work. Adds pre-mortem (3 scenarios) and expanded test planning (unit/integration/e2e/observability). Without this flag, deliberate mode can still auto-enable when the request explicitly signals high risk (auth/security, migrations, destructive changes, production incidents, compliance/PII, public API breakage).

## Usage with interactive mode

```
$ralplan --interactive "task description"
```

## Behavior

## GPT-5.4 Guidance Alignment

- Default to concise, evidence-dense progress and completion reporting unless the user or risk level requires more detail.
- Treat newer user task updates as local overrides for the active workflow branch while preserving earlier non-conflicting constraints.
- If correctness depends on additional inspection, retrieval, execution, or verification, keep using the relevant tools until the consensus-planning flow is grounded.
- Right-size implementation steps and PRD story counts to the actual scope; do not default to exactly five steps when the task is clearly smaller or larger.
- Continue through clear, low-risk, reversible next steps automatically; ask only when the next step is materially branching, destructive, or preference-dependent.

This skill invokes the Plan skill in consensus mode:

```
$plan --consensus <arguments>
$plan --consensus --interactive <arguments>
```

When invoked through Hermes MCP, the `ralplan` tool returns a self-contained
prompt package focused on the consensus flow. The MCP server does not execute
the multi-agent loop internally; the host agent must consume the returned
`invocation_message` and follow the workflow.

The Planner / Architect / Critic role skills are **not** inlined into the
`ralplan` response. Fetch them on demand with the Hermes MCP
`bundled_skill_read` tool:

- `bundled_skill_read(name="plan")` for the base planning workflow.
- `bundled_skill_read(name="planner")` for the Planner perspective pass.
- `bundled_skill_read(name="architect")` for the Architect perspective pass.
- `bundled_skill_read(name="critic")` for the Critic perspective pass.

### Pure MCP Host Compatibility

Assume the MCP host does **not** have `AskUserQuestion`, `ask_codex`, or
Planner / Architect / Critic subagent tools. The role skills fetched through
`bundled_skill_read` are prompt instructions to apply sequentially in the same
host context, not external agents.

For pure MCP hosts:
- Ask required user questions in normal chat instead of `AskUserQuestion`.
- Fetch `planner`, `architect`, and `critic` with `bundled_skill_read` only
  when needed, then perform those passes sequentially in the same host agent
  context. Do not claim that external reviewers or subagents ran.
- Treat `ask_codex(agent_role=...)` as an instruction to switch perspective
  locally: first draft as Planner, then review as Architect, then evaluate as
  Critic.
- Use host-native file/search/shell abilities if available. If not available,
  base the plan only on evidence already present in the conversation or from
  Hermes MCP tools such as `task_context_bundle`, `session_recall_search`,
  `memory_read`, `skills_list`, and `skill_view_safe`.
- Do not call nonexistent Hermes MCP tools such as `AskUserQuestion`,
  `ask_codex`, `Read`, `Write`, `Grep`, `Glob`, or `Bash`.
- For execution handoff from a pure MCP host, call the Hermes `ralph` MCP tool
  with the approved plan summary to obtain a new `invocation_message`, then
  submit that message to the host agent.
- Plan persistence is host-owned. The Hermes MCP `ralplan` tool itself does not
  write plan files, review files, host project files, or source files. The workflow
  **must still produce a plan Markdown document**. If the host can write files,
  save it using host-owned storage; otherwise output the same Markdown in chat
  under `# Current Approved Plan`. Do not assume Hermes MCP will create or
  manage plan storage for the host.

### Required Plan Markdown Output

Every ralplan run must produce a concrete Markdown plan document before any
`ralph` handoff. The document is the handoff contract and must be specific
enough that execution can proceed without guessing.

Use this structure:

```md
# Plan: <task title>

## Requirements Summary
- Goal:
- Scope:
- Non-goals:
- Constraints:
- Evidence used:

## Current Priority Order
| Priority | Step | Why now | Depends on | Status |
|---|---|---|---|---|
| P0 | ... | ... | ... | planned |

## Acceptance Criteria
- [ ] Concrete, testable criterion

## Implementation Steps
1. [P0] Step title
   - Files/areas:
   - Action:
   - Expected result:
   - Verification:

## RALPLAN-DR Summary
- Principles:
- Decision Drivers:
- Options considered:
- Chosen option and invalidation rationale:

## ADR
- Decision:
- Drivers:
- Alternatives considered:
- Why chosen:
- Consequences:
- Follow-ups:

## Risks and Mitigations
| Risk | Impact | Mitigation |
|---|---|---|

## Verification Plan
- Unit:
- Integration:
- E2E/manual:
- Observability/logging:

## Ralph MCP Handoff
- Approved plan summary to pass to `ralph`:
- Constraints to preserve:
- Expected verification evidence:
```

Keep priorities live: if Architect or Critic feedback changes the order,
update `Current Priority Order` and the `[P0/P1/P2]` tags in Implementation
Steps before approval.

The consensus workflow:
1. **Planner** creates initial plan and a compact **RALPLAN-DR summary** before review:
   - Principles (3-5)
   - Decision Drivers (top 3)
   - Viable Options (>=2) with bounded pros/cons
   - If only one viable option remains, explicit invalidation rationale for alternatives
   - Deliberate mode only: pre-mortem (3 scenarios) + expanded test plan (unit/integration/e2e/observability)
2. **User feedback** *(--interactive only)*: If `--interactive` is set, ask the user in normal chat to review the draft plan **plus the Principles / Drivers / Options summary** before review (Proceed to review / Request changes / Skip review). Otherwise, automatically proceed to review.
3. **Architect perspective** reviews for architectural soundness and must provide the strongest steelman antithesis, at least one real tradeoff tension, and (when possible) synthesis — complete this pass before step 4. In deliberate mode, explicitly flag principle violations.
4. **Critic perspective** evaluates against quality criteria — run only after step 3 completes. Critic must enforce principle-option consistency, fair alternatives, risk mitigation clarity, testable acceptance criteria, and concrete verification steps. In deliberate mode, Critic must reject missing/weak pre-mortem or expanded test plan.
5. **Re-review loop** (max 5 iterations): Any non-`APPROVE` Critic verdict (`ITERATE` or `REJECT`) MUST run the same full closed loop:
   a. Collect Architect + Critic feedback
   b. Revise the plan with Planner
   c. Return to Architect review
   d. Return to Critic evaluation
   e. Repeat this loop until Critic returns `APPROVE` or 5 iterations are reached
   f. If 5 iterations are reached without `APPROVE`, present the best version to the user
6. On Critic approval, finalize the plan Markdown document. It must include Requirements Summary, Current Priority Order, Acceptance Criteria, Implementation Steps, RALPLAN-DR Summary, ADR, Risks and Mitigations, Verification Plan, and Ralph MCP Handoff. If `--interactive` is set, ask the user in normal chat to choose (Approve and execute via ralph / Request changes / Reject). Otherwise, output the final plan and stop.
7. *(--interactive only)* User chooses: Approve via ralph, Request changes, or Reject
8. *(--interactive only)* On approval: call the Hermes `ralph` MCP tool with the approved plan summary to obtain the next `invocation_message`; never implement directly.

> **Important:** Steps 3 and 4 MUST run sequentially. Do NOT issue both agent calls in the same parallel batch. Always await the Architect result before invoking Critic.

Follow the Plan skill's full documentation for consensus mode details.

## Pre-context Intake

Before consensus planning or execution handoff, ensure a grounded context snapshot exists:

1. Derive a task slug from the request.
2. Reuse the latest relevant host-owned context snapshot when available.
3. If none exists and the host has file-write capability, create a host-owned
   context snapshot with:
   - task statement
   - desired outcome
   - known facts/evidence
   - constraints
   - unknowns/open questions
   - likely codebase touchpoints
4. If the host cannot write files, keep the snapshot in the conversation and label it "Context Snapshot".
5. If ambiguity remains high, gather brownfield facts first using available host tools and Hermes MCP tools (`task_context_bundle`, `session_recall_search`, `memory_read`, `skills_list`, `skill_view_safe`). If no inspection tools are available, ask one focused clarification question in normal chat before continuing.

Do not hand off to execution modes until this intake is complete; if urgency forces progress, explicitly document the risk tradeoffs.

## Pre-Execution Gate

### Why the Gate Exists

Execution wrappers such as `ralph` and `autopilot` require a clear target. When launched on a vague request like "ralph improve the app", the host agent has no clear scope — it wastes cycles on discovery that should happen during planning, often delivering partial or misaligned work that requires rework.

The ralplan-first gate intercepts underspecified execution requests and redirects them through the ralplan consensus planning workflow. This ensures:
- **Explicit scope**: A PRD defines exactly what will be built
- **Test specification**: Acceptance criteria are testable before code is written
- **Consensus**: Planner, Architect, and Critic agree on the approach
- **No wasted execution**: Agents start with a clear, bounded task

### Good vs Bad Prompts

**Passes the gate** (specific enough for direct execution):
- `ralph fix the null check in src/hooks/bridge.ts:326`
- `autopilot implement issue #42`
- `ralph do:\n1. Add input validation\n2. Write tests\n3. Update README`

**Gated — redirected to ralplan** (needs scoping first):
- `ralph fix this`
- `autopilot build the app`
- `ralph add authentication`

**Bypass the gate** (when you know what you want):
- `force: ralph refactor the auth module`
- `! autopilot optimize everything`

### When the Gate Does NOT Trigger

The gate auto-passes when it detects **any** concrete signal. You do not need all of them — one is enough:

| Signal Type | Example prompt | Why it passes |
|---|---|---|
| File path | `ralph fix src/hooks/bridge.ts` | References a specific file |
| Issue/PR number | `ralph implement #42` | Has a concrete work item |
| camelCase symbol | `ralph fix processKeywordDetector` | Names a specific function |
| PascalCase symbol | `ralph update UserModel` | Names a specific class |
| snake_case symbol | `ralph fix user_model` | Names a specific identifier |
| Test runner | `ralph npm test && fix failures` | Has an explicit test target |
| Numbered steps | `ralph do:\n1. Add X\n2. Test Y` | Structured deliverables |
| Acceptance criteria | `ralph add login - acceptance criteria: ...` | Explicit success definition |
| Error reference | `ralph fix TypeError in auth` | Specific error to address |
| Code block | `ralph add: \`\`\`ts ... \`\`\`` | Concrete code provided |
| Escape prefix | `force: ralph do it` or `! ralph do it` | Explicit user override |

### End-to-End Flow Example

1. User types: `ralph add user authentication`
2. Gate detects: execution keyword (`ralph`) + underspecified prompt (no files, functions, or test spec)
3. Gate redirects to **ralplan** with message explaining the redirect
4. Ralplan consensus runs:
   - **Planner** creates initial plan (which files, what auth method, what tests)
   - **Architect** reviews for soundness
   - **Critic** validates quality and testability
5. On consensus approval, call the Hermes `ralph` MCP tool with the approved
   plan summary to obtain a follow-up `invocation_message`.
6. The host agent continues with a clear, bounded plan in the same MCP-hosted
   workflow.

### Troubleshooting

| Issue | Solution |
|-------|----------|
| Gate fires on a well-specified prompt | Add a file reference, function name, or issue number to anchor the request |
| Want to bypass the gate | Prefix with `force:` or `!` (e.g., `force: ralph fix it`) |
| Gate does not fire on a vague prompt | The gate only catches prompts with <=15 effective words and no concrete anchors; add more detail or use `$ralplan` explicitly |
| Redirected to ralplan but want to skip planning | In the ralplan workflow, say "just do it" or "skip planning" to transition directly to execution |

## Scenario Examples

**Good:** The user says `continue` after the workflow already has a clear next step. Continue the current branch of work instead of restarting or re-asking the same question.

**Good:** The user changes only the output shape or downstream delivery step (for example `make a PR`). Preserve earlier non-conflicting workflow constraints and apply the update locally.

**Bad:** The user says `continue`, and the workflow restarts discovery or stops before the missing verification/evidence is gathered.
