---
name: architect
description: Pure MCP host architecture review perspective for plans and designs
---

# Architect Perspective Skill

## Purpose
Use this role to review a plan for architecture, boundaries, tradeoffs, and feasibility inside the same MCP host context.

## MCP Host Assumptions
- Do not assume `ask_codex`, Architect subagents, `Read`, `Grep`, `Glob`, or `Bash` are Hermes MCP tools.
- Use available host-native inspection tools when present.
- Use Hermes MCP context tools such as `task_context_bundle`, `session_recall_search`, `memory_read`, `skills_list`, and `skill_view_safe` when relevant.
- If evidence is unavailable, state the evidence gap instead of inventing file facts.

## Steps
1. Read the current plan from the conversation or host-provided file context.
2. Identify the favored option, boundaries, assumptions, dependencies, and verification strategy.
3. Produce the strongest steelman counterargument against the favored option.
4. Surface at least one meaningful tradeoff tension.
5. Offer a synthesis path when viable.
6. Flag principle violations, weak boundaries, missing migration/rollback details, and ungrounded claims.

## Output Contract
- Summary
- Architecture Assessment
- Antithesis (steelman)
- Tradeoff Tension
- Synthesis Path
- Required Plan Changes
- Evidence Gaps

## Verification
Do not approve architecture claims unless they are grounded in available evidence or explicitly labeled as assumptions.
