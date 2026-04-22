"""MCP-facing wrappers for bundled Hermes skills.

The files under ``my_skills/`` are prompt/workflow assets.  MCP clients, however,
only discover callable capabilities through MCP tools.  This module bridges
that boundary by turning selected bundled skills into structured tool results
that an MCP host can feed back to its agent as the next instruction.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict


BUNDLED_MCP_SKILLS: Dict[str, str] = {
    "autopilot": "autopilot",
    "deep-interview": "deep-interview",
    "plan": "plan",
    "planner": "planner",
    "architect": "architect",
    "critic": "critic",
    "ralph": "ralph",
    "ralplan": "ralplan",
}

RALPLAN_REQUIRED_SKILLS = (
    ("plan", "Base planning workflow and consensus-mode details."),
    ("planner", "Planner perspective pass for drafting and revising the plan."),
    ("architect", "Architect perspective pass for tradeoffs and design review."),
    ("critic", "Critic perspective pass for plan quality and testability review."),
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _repo_relative(path: Path) -> str:
    return str(path.relative_to(_repo_root()).as_posix())


def _bundled_skill_dir(skill_name: str) -> Path:
    normalized = skill_name.strip().lower().replace("_", "-")
    directory = BUNDLED_MCP_SKILLS.get(normalized)
    if not directory:
        raise ValueError(
            f"Unsupported bundled MCP skill: {skill_name}. "
            f"Available: {', '.join(sorted(BUNDLED_MCP_SKILLS))}"
        )
    return _repo_root() / "my_skills" / directory


def _load_bundled_skill(skill_name: str) -> tuple[dict[str, Any], Path]:
    skill_dir = _bundled_skill_dir(skill_name)
    skill_path = skill_dir / "SKILL.md"
    if not skill_path.exists():
        raise FileNotFoundError(f"Bundled skill file not found: {skill_path}")

    content = skill_path.read_text(encoding="utf-8")
    try:
        from agent.skill_utils import parse_frontmatter

        frontmatter, _body = parse_frontmatter(content)
    except Exception:
        frontmatter = {}

    return {
        "success": True,
        "name": str(frontmatter.get("name") or skill_dir.name),
        "description": str(frontmatter.get("description") or ""),
        "content": content,
        "frontmatter": frontmatter,
    }, skill_dir


def build_bundled_skill_invocation(
    skill_name: str,
    instruction: str = "",
    *,
    runtime_note: str = "",
) -> Dict[str, Any]:
    """Build a structured MCP result for invoking a bundled Hermes skill.

    The result intentionally does not execute the skill.  MCP tools cannot
    mutate a host model's system prompt directly, so clients should submit the
    returned ``invocation_message`` to their agent as the next user instruction.
    """
    loaded_skill, skill_dir = _load_bundled_skill(skill_name)
    resolved_name = str(loaded_skill.get("name") or skill_name)

    from agent.skill_commands import _build_skill_message

    activation_note = (
        f'[SYSTEM: The user has invoked the "{resolved_name}" skill through '
        "Hermes MCP, indicating they want you to follow its instructions. "
        "The full skill content is loaded below.]"
    )
    invocation_message = _build_skill_message(
        loaded_skill,
        skill_dir,
        activation_note,
        user_instruction=instruction,
        runtime_note=runtime_note,
    )

    return {
        "success": True,
        "skill": resolved_name,
        "source": _repo_relative(skill_dir),
        "description": loaded_skill.get("description", ""),
        "invocation_message": invocation_message,
        "client_action": (
            "Submit invocation_message to the MCP host agent as the next "
            "instruction, then follow the skill workflow. This tool prepares "
            "the skill prompt; it does not execute the workflow by itself."
        ),
    }


def _append_required_skill_manifest(
    result: Dict[str, Any],
    required_skills: tuple[tuple[str, str], ...],
) -> Dict[str, Any]:
    """Append on-demand role-skill retrieval instructions without inlining them."""
    root = _repo_root()
    manifest: list[dict[str, str]] = []
    for name, purpose in required_skills:
        skill_dir = _bundled_skill_dir(name)
        skill_path = skill_dir / "SKILL.md"
        if not skill_path.exists():
            raise FileNotFoundError(f"Bundled workflow skill not found: {name}")
        manifest.append(
            {
                "name": name,
                "path": _repo_relative(skill_path),
                "fetch_tool": "bundled_skill_read",
                "fetch_args": f'{{"name": "{name}"}}',
                "purpose": purpose,
            }
        )

    sections = [
        "",
        "[Bundled role skills for on-demand retrieval]",
        (
            "Do not assume Planner / Architect / Critic subagents exist. Fetch "
            "these bundled skills only when needed by calling the Hermes MCP "
            "`bundled_skill_read` tool, then apply each skill as a sequential "
            "perspective pass in the same host context."
        ),
    ]
    for item in manifest:
        sections.append(
            f'- `{item["name"]}`: call `bundled_skill_read(name="{item["name"]}")` '
            f'to fetch `{item["path"]}`. Purpose: {item["purpose"]}'
        )

    result["invocation_message"] = str(result["invocation_message"]).rstrip() + "\n" + "\n".join(sections)
    result["required_bundled_skills"] = manifest
    result["client_action"] = (
        "Submit invocation_message to the MCP host agent. When the workflow "
        "needs role guidance, call bundled_skill_read for the required skill "
        "name (plan, planner, architect, critic) and apply it as an in-context "
        "perspective pass. This tool does not execute the workflow by itself."
    )
    return result


def autopilot_invocation(instruction: str) -> Dict[str, Any]:
    return build_bundled_skill_invocation("autopilot", instruction)


def deep_interview_invocation(
    instruction: str,
    *,
    depth: str = "standard",
    autoresearch: bool = False,
) -> Dict[str, Any]:
    depth = (depth or "standard").strip().lower()
    if depth not in {"quick", "standard", "deep"}:
        raise ValueError("depth must be one of: quick, standard, deep")

    flags = []
    if depth != "standard":
        flags.append(f"--{depth}")
    if autoresearch:
        flags.append("--autoresearch")
    full_instruction = " ".join([*flags, instruction]).strip()
    return build_bundled_skill_invocation("deep-interview", full_instruction)


def ralph_invocation(instruction: str) -> Dict[str, Any]:
    return build_bundled_skill_invocation("ralph", instruction)


def plan_invocation(
    instruction: str,
    *,
    mode: str = "auto",
    interactive: bool = False,
    deliberate: bool = False,
    review: bool = False,
) -> Dict[str, Any]:
    mode = (mode or "auto").strip().lower()
    if mode not in {"auto", "direct", "consensus", "review"}:
        raise ValueError("mode must be one of: auto, direct, consensus, review")

    flags = []
    if review or mode == "review":
        flags.append("--review")
    elif mode == "direct":
        flags.append("--direct")
    elif mode == "consensus":
        flags.append("--consensus")

    if interactive:
        flags.append("--interactive")
    if deliberate:
        flags.append("--deliberate")

    full_instruction = " ".join([*flags, instruction]).strip()
    return build_bundled_skill_invocation("plan", full_instruction)


def ralplan_invocation(
    instruction: str,
    *,
    interactive: bool = False,
    deliberate: bool = False,
    runtime_note: str = "",
) -> Dict[str, Any]:
    flags = []
    if interactive:
        flags.append("--interactive")
    if deliberate:
        flags.append("--deliberate")
    full_instruction = " ".join([*flags, instruction]).strip()
    result = build_bundled_skill_invocation("ralplan", full_instruction, runtime_note=runtime_note)
    return _append_required_skill_manifest(result, RALPLAN_REQUIRED_SKILLS)
