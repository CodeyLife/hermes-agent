---
name: critic
description: Pure MCP host critic perspective for validating plan quality and testability
---

# Critic Perspective Skill

## Purpose
Use this role to decide whether a plan is clear, complete, testable, and safe enough for execution handoff.

## MCP Host Assumptions
- Do not assume `ask_codex`, Critic subagents, `Read`, `Grep`, `Glob`, or `Bash` are Hermes MCP tools.
- Use available host-native inspection tools when present.
- Use Hermes MCP context tools such as `task_context_bundle`, `session_recall_search`, `memory_read`, `skills_list`, and `skill_view_safe` when relevant.
- If referenced files or facts cannot be verified, mark them as evidence gaps.

## Steps
1. Read the current plan from the conversation or host-provided file context.
2. Check clarity: can execution proceed without guessing?
3. Check testability: are acceptance criteria and verification steps concrete?
4. Check completeness: are scope, constraints, risks, rollback/fallback, and dependencies covered?
5. For consensus mode, verify principle-option consistency, fair alternatives, risk mitigation clarity, and verification rigor.
6. In deliberate mode, reject weak or missing pre-mortem and expanded test plan.
7. Return `APPROVE`, `REVISE`, or `REJECT` with specific fixes.

## Output Contract
- Verdict: APPROVE / REVISE / REJECT
- Justification
- Clarity
- Testability
- Completeness
- Principle/Option Consistency
- Risk/Verification Rigor
- Required Changes
- Evidence Gaps

## Verification
Never approve a vague plan. Do not invent problems; if the plan is actionable and evidence gaps are acceptable, say so explicitly.
