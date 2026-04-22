---
name: plan
description: Strategic planning with optional interview workflow
---

<Purpose>
Plan creates comprehensive, actionable work plans through normal host interaction. It auto-detects whether to interview the user (broad requests) or plan directly (detailed requests), and supports consensus mode (sequential Planner / Architect / Critic perspective passes with RALPLAN-DR structured deliberation) and review mode (Critic-perspective evaluation of existing plans).
</Purpose>

<Use_When>
- User wants to plan before implementing -- "plan this", "plan the", "let's plan"
- User wants structured requirements gathering for a vague idea
- User wants an existing plan reviewed -- "review this plan", `--review`
- User wants multi-perspective consensus on a plan -- `--consensus`, "ralplan"
- Task is broad or vague and needs scoping before any code is written
</Use_When>

<Do_Not_Use_When>
- User wants autonomous end-to-end execution -- use `autopilot` instead
- User wants to start coding immediately with a clear task -- use `ralph` or delegate to executor
- User asks a simple question that can be answered directly -- just answer it
- Task is a single focused fix with obvious scope -- skip planning, just do it
</Do_Not_Use_When>

<Why_This_Exists>
Jumping into code without understanding requirements leads to rework, scope creep, and missed edge cases. Plan provides structured requirements gathering, expert analysis, and quality-gated plans so that execution starts from a solid foundation. The consensus mode adds multi-perspective validation for high-stakes projects.
</Why_This_Exists>

<Execution_Policy>
- Auto-detect interview vs direct mode based on request specificity
- Ask one question at a time during interviews -- never batch multiple questions
- Gather codebase facts before asking the user about facts the host can inspect. In a pure MCP host, use available host tools and Hermes MCP tools such as `task_context_bundle`, `session_recall_search`, `memory_read`, `skills_list`, and `skill_view_safe`; if no inspection tool is available, ask one focused clarification question in normal chat.
- Plans must meet quality standards: 80%+ claims cite file/line, 90%+ criteria are testable
- Implementation step count must be right-sized to task scope; avoid defaulting to exactly five steps when the work is clearly smaller or larger
- Consensus mode outputs the final plan by default; add `--interactive` to ask the user in normal chat before execution handoff
- Consensus mode uses RALPLAN-DR short mode by default; switch to deliberate mode with `--deliberate` or when the request explicitly signals high risk (auth/security, data migration, destructive/irreversible changes, production incident, compliance/PII, public API breakage)
- Default to concise, evidence-dense progress and completion reporting unless the user or risk level requires more detail
- Treat newer user task updates as local overrides for the active workflow branch while preserving earlier non-conflicting constraints
- If correctness depends on additional inspection, retrieval, execution, or verification, keep using the relevant tools until the plan is grounded
- Continue through clear, low-risk, reversible next steps automatically; ask only when the next step is materially branching, destructive, or preference-dependent
</Execution_Policy>

<Steps>

### Mode Selection

| Mode | Trigger | Behavior |
|------|---------|----------|
| Interview | Default for broad requests | Interactive requirements gathering |
| Direct | `--direct`, or detailed request | Skip interview, generate plan directly |
| Consensus | `--consensus`, "ralplan" | Sequential Planner -> Architect -> Critic perspective passes until agreement with RALPLAN-DR structured deliberation (short by default, `--deliberate` for high-risk); outputs plan by default |
| Consensus Interactive | `--consensus --interactive` | Same as Consensus but asks for user feedback in normal chat at draft and approval steps, then hands off via the `ralph` MCP wrapper |
| Review | `--review`, "review this plan" | Critic-perspective evaluation of existing plan |

### Interview Mode (broad/vague requests)

1. **Classify the request**: Broad (vague verbs, no specific files, touches 3+ areas) triggers interview mode
2. **Ask one focused question** in normal chat for preferences, scope, and constraints
3. **Gather codebase facts first**: Before asking "what patterns does your code use?", inspect with available host tools and Hermes MCP tools. If no inspection tools are available, ask one focused clarification question instead.
4. **Build on answers**: Each question builds on the previous answer
5. **Analyst perspective pass**: In the same host context, check for hidden requirements, edge cases, and risks
6. **Create plan** when the user signals readiness: "create the plan", "I'm ready", "make it a work plan"

### Direct Mode (detailed requests)

1. **Quick Analysis**: Optional brief Analyst-perspective pass
2. **Create plan**: Generate comprehensive work plan immediately
3. **Review** (optional): Critic-perspective review if requested

### Consensus Mode (`--consensus` / "ralplan")

**RALPLAN-DR modes**: **Short** (default, bounded structure) and **Deliberate** (for `--deliberate` or explicit high-risk requests). Both modes keep the same sequential Planner -> Architect -> Critic perspective sequence. The workflow auto-proceeds through planning steps but outputs the final plan without executing.

1. **Planner** creates initial plan and a compact **RALPLAN-DR summary** before any Architect review. The summary **MUST** include:
   - **Principles** (3-5)
   - **Decision Drivers** (top 3)
   - **Viable Options** (>=2) with bounded pros/cons for each option
   - If only one viable option remains, an explicit **invalidation rationale** for the alternatives that were rejected
   - In **deliberate mode**: a **pre-mortem** (3 failure scenarios) and an **expanded test plan** covering **unit / integration / e2e / observability**
2. **User feedback** *(--interactive only)*: If running with `--interactive`, **MUST** ask the user in normal chat to review the draft plan **plus the RALPLAN-DR Principles / Decision Drivers / Options summary for early direction alignment** with these options:
   - **Proceed to review** — continue to Architect and Critic perspective evaluation
   - **Request changes** — return to step 1 with user feedback incorporated
   - **Skip review** — go directly to final approval (step 7)
   If NOT running with `--interactive`, automatically proceed to review (step 3).
3. **Architect perspective** reviews for architectural soundness in the same host context. The review **MUST** include: strongest steelman counterargument (antithesis) against the favored option, at least one meaningful tradeoff tension, and (when possible) a synthesis path. In deliberate mode, explicitly flag principle violations. Complete this step before proceeding to step 4.
4. **Critic perspective** evaluates against quality criteria in the same host context. Critic **MUST** verify principle-option consistency, fair alternative exploration, risk mitigation clarity, testable acceptance criteria, and concrete verification steps. Critic **MUST** explicitly reject shallow alternatives, driver contradictions, vague risks, or weak verification. In deliberate mode, Critic **MUST** reject missing/weak pre-mortem or missing/weak expanded test plan. Run only after step 3 is complete.
5. **Re-review loop** (max 5 iterations): If Critic rejects or iterates, execute this closed loop:
   a. Collect all feedback from Architect + Critic
   b. Pass feedback to Planner to produce a revised plan
   c. **Return to Step 3** — Architect reviews the revised plan
   d. **Return to Step 4** — Critic evaluates the revised plan
   e. Repeat until Critic approves OR max 5 iterations reached
   f. If max iterations are reached without approval, present the best version to the user in normal chat with a note that perspective consensus was not reached
6. **Apply improvements**: When reviewers approve with improvement suggestions, merge all accepted improvements into the plan file before proceeding. Final consensus output **MUST** include an **ADR** section with: **Decision**, **Drivers**, **Alternatives considered**, **Why chosen**, **Consequences**, **Follow-ups**. Specifically:
   a. Collect all improvement suggestions from Architect and Critic responses
   b. Deduplicate and categorize the suggestions
   c. If the host has file-write capability, update the plan file in `.omx/plans/` with the accepted improvements; otherwise update the plan in the conversation output
   d. Note which improvements were applied in a brief changelog section at the end of the plan
   e. Before any execution handoff, add concrete **follow-up guidance for the `ralph` MCP wrapper**: what approved plan summary to pass, what verification evidence `ralph` should produce, and what constraints it must preserve
7. On Critic approval (with improvements applied): *(--interactive only)* If running with `--interactive`, ask the user in normal chat to choose:
   - **Approve and execute via ralph** — call the Hermes `ralph` MCP tool with the approved plan summary to obtain the next `invocation_message`
   - **Request changes** — return to step 1 with user feedback
   - **Reject** — discard the plan entirely
   If NOT running with `--interactive`, output the final approved plan and stop. Do NOT auto-execute.
8. *(--interactive only)* User chooses in normal chat.
9. On user approval (--interactive only): call the Hermes `ralph` MCP tool with the approved plan summary and constraints to obtain the next `invocation_message`. Do NOT implement directly in the planning prompt.

### Review Mode (`--review`)

0. Treat review as a reviewer-only pass. The context that wrote the plan, cleanup proposal, or diff MUST NOT be the context that approves it.
1. Read plan file from `.omx/plans/`
2. Evaluate from a Critic perspective in the same host context
3. For cleanup/refactor/anti-slop work, verify that the artifact includes a cleanup plan, regression tests or an explicit test gap, smell-by-smell passes, and quality gates.
4. Return verdict: APPROVED, REVISE (with specific feedback), or REJECT (replanning required)
5. If the current context authored the artifact, explicitly separate the review pass: restate the artifact, switch to Critic perspective, and do not approve without evidence.

### Plan Output Format

Every plan includes:
- Requirements Summary
- Acceptance Criteria (testable)
- Implementation Steps (with file references)
- Adaptive step count sized to the actual scope (not a fixed five-step template)
- Risks and Mitigations
- Verification Steps
- For consensus/ralplan: **RALPLAN-DR summary** (Principles, Decision Drivers, Options)
- For consensus/ralplan final output: **ADR** (Decision, Drivers, Alternatives considered, Why chosen, Consequences, Follow-ups)
- For consensus/ralplan execution handoff: **Ralph MCP wrapper handoff guidance** (approved plan summary, constraints to preserve, and verification evidence expected from `ralph`)
- For deliberate consensus mode: **Pre-mortem (3 scenarios)** and **Expanded Test Plan** (unit/integration/e2e/observability)

When file-write capability is available, plans are saved to `.omx/plans/` and drafts go to `.omx/drafts/`. In pure MCP hosts without file-write capability, keep the plan in the conversation output and clearly label it as the current approved plan.
</Steps>

<Tool_Usage>
- Assume a pure MCP host does not have `AskUserQuestion`, `ask_codex`, Planner/Architect/Critic subagents, `ToolSearch`, or OMX runtime tools. Do not call those names as Hermes MCP tools.
- Ask preference questions (scope, priority, timeline, risk tolerance) in normal chat, one focused question at a time.
- Use plain text for questions needing specific values (port numbers, names, follow-up clarifications).
- Gather codebase facts with available host tools and Hermes MCP tools such as `task_context_bundle`, `session_recall_search`, `memory_read`, `skills_list`, and `skill_view_safe` before asking the user about facts.
- Perform planner, analyst, architect, and critic work as sequential perspective passes in the same host context. Do not claim external reviewers, subagents, or `ask_codex` calls ran.
- **CRITICAL — Consensus perspective passes MUST be sequential, never parallel.** Complete the Architect perspective before starting the Critic perspective.
- In consensus mode, default to RALPLAN-DR short mode; enable deliberate mode on `--deliberate` or explicit high-risk signals (auth/security, migrations, destructive changes, production incidents, compliance/PII, public API breakage).
- In consensus mode with `--interactive`: ask the user in normal chat for the feedback step (step 2) and final approval step (step 7). Without `--interactive`, auto-proceed through planning perspective passes without pausing. Output the final plan without execution.
- In consensus mode with `--interactive`, on user approval call the Hermes `ralph` MCP tool for execution handoff and submit its returned `invocation_message` to the host agent. Do not implement directly in the planning prompt.
- Execution follow-up handoff MUST include the approved plan summary, constraints to preserve, and verification evidence expected from `ralph`.
</Tool_Usage>


## Scenario Examples

**Good:** The user says `continue` after the workflow already has a clear next step. Continue the current branch of work instead of restarting or re-asking the same question.

**Good:** The user changes only the output shape or downstream delivery step (for example `make a PR`). Preserve earlier non-conflicting workflow constraints and apply the update locally.

**Bad:** The user says `continue`, and the workflow restarts discovery or stops before the missing verification/evidence is gathered.

<Examples>
<Good>
Adaptive interview (gathering facts before asking):
```
Planner: [uses available host/Hermes MCP tools to inspect authentication implementation]
Planner: [observes: "Auth is in src/auth/ using JWT with passport.js"]
Planner: "I see you're using JWT authentication with passport.js in src/auth/.
         For this new feature, should we extend the existing auth or add a separate auth flow?"
```
Why good: Answers its own codebase question first, then asks an informed preference question.
</Good>

<Good>
Single question at a time:
```
Q1: "What's the main goal?"
A1: "Improve performance"
Q2: "For performance, what matters more -- latency or throughput?"
A2: "Latency"
Q3: "For latency, are we optimizing for p50 or p99?"
```
Why good: Each question builds on the previous answer. Focused and progressive.
</Good>

<Bad>
Asking about things you could look up:
```
Planner: "Where is authentication implemented in your codebase?"
User: "Uh, somewhere in src/auth I think?"
```
Why bad: The planner should inspect with available host/Hermes MCP tools first, not ask the user for discoverable facts.
</Bad>

<Bad>
Batching multiple questions:
```
"What's the scope? And the timeline? And who's the audience?"
```
Why bad: Three questions at once causes shallow answers. Ask one at a time.
</Bad>

<Bad>
Presenting all design options at once:
```
"Here are 4 approaches: Option A... Option B... Option C... Option D... Which do you prefer?"
```
Why bad: Decision fatigue. Present one option with trade-offs, get reaction, then present the next.
</Bad>
</Examples>

<Escalation_And_Stop_Conditions>
- Stop interviewing when requirements are clear enough to plan -- do not over-interview
- In consensus mode, stop after 5 Planner/Architect/Critic iterations and present the best version
- Consensus mode outputs the plan by default; with `--interactive`, user can approve and hand off via the Hermes `ralph` MCP wrapper
- If the user says "just do it" or "skip planning", call the Hermes `ralph` MCP tool to obtain the execution `invocation_message`. Do NOT implement directly in the planning prompt.
- Escalate to the user when there are irreconcilable trade-offs that require a business decision
</Escalation_And_Stop_Conditions>

<Final_Checklist>
- [ ] Plan has testable acceptance criteria (90%+ concrete)
- [ ] Plan references specific files/lines where applicable (80%+ claims)
- [ ] All risks have mitigations identified
- [ ] No vague terms without metrics ("fast" -> "p99 < 200ms")
- [ ] Plan saved to `.omx/plans/` when file-write capability is available, or clearly labeled in conversation output when not
- [ ] In consensus mode: RALPLAN-DR summary includes 3-5 principles, top 3 drivers, and >=2 viable options (or explicit invalidation rationale)
- [ ] In consensus mode final output: ADR section included (Decision / Drivers / Alternatives considered / Why chosen / Consequences / Follow-ups)
- [ ] In deliberate consensus mode: pre-mortem (3 scenarios) + expanded test plan (unit/integration/e2e/observability) included
- [ ] In consensus mode with `--interactive`: user explicitly approved before any execution; without `--interactive`: output final plan after Critic approval (no auto-execution)
</Final_Checklist>

<Advanced>
## Design Option Presentation

When presenting design choices during interviews, chunk them:

1. **Overview** (2-3 sentences)
2. **Option A** with trade-offs
3. [Wait for user reaction]
4. **Option B** with trade-offs
5. [Wait for user reaction]
6. **Recommendation** (only after options discussed)

Format for each option:
```
### Option A: [Name]
**Approach:** [1 sentence]
**Pros:** [bullets]
**Cons:** [bullets]

What's your reaction to this approach?
```

## Question Classification

Before asking any interview question, classify it:

| Type | Examples | Action |
|------|----------|--------|
| Codebase Fact | "What patterns exist?", "Where is X?" | Explore first, do not ask user |
| User Preference | "Priority?", "Timeline?" | Ask user in normal chat |
| Scope Decision | "Include feature Y?" | Ask user |
| Requirement | "Performance constraints?" | Ask user |

## Review Quality Criteria

| Criterion | Standard |
|-----------|----------|
| Clarity | 80%+ claims cite file/line |
| Testability | 90%+ criteria are concrete |
| Verification | All file refs exist |
| Specificity | No vague terms |

## Deprecation Notice

The separate `/planner`, `/ralplan`, and `/review` skills have been merged into `$plan`. All workflows (interview, direct, consensus, review) are available through `$plan`.
</Advanced>
