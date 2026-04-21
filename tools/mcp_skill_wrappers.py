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
    "ralph": "ralph",
    "ralplan": "ralplan",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


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
        "source": str(skill_dir.relative_to(_repo_root()).as_posix()),
        "description": loaded_skill.get("description", ""),
        "invocation_message": invocation_message,
        "client_action": (
            "Submit invocation_message to the MCP host agent as the next "
            "instruction, then follow the skill workflow. This tool prepares "
            "the skill prompt; it does not execute the workflow by itself."
        ),
    }


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


def ralplan_invocation(
    instruction: str,
    *,
    interactive: bool = False,
    deliberate: bool = False,
) -> Dict[str, Any]:
    flags = []
    if interactive:
        flags.append("--interactive")
    if deliberate:
        flags.append("--deliberate")
    full_instruction = " ".join([*flags, instruction]).strip()
    return build_bundled_skill_invocation("ralplan", full_instruction)
