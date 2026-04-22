#!/usr/bin/env python3
"""Bundled Hermes skill helpers for MCP clients."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict


_ALLOWED_BUNDLED_SKILLS = {
    "autopilot",
    "deep-interview",
    "plan",
    "planner",
    "architect",
    "critic",
    "ralph",
    "ralplan",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _normalize_relpath(path: Path) -> str:
    return path.as_posix()


def read_bundled_skill(skill_name: str) -> Dict[str, Any]:
    """Return a repo-bundled skill by name for MCP guidance."""
    normalized = str(skill_name or "").strip().lower()
    if normalized not in _ALLOWED_BUNDLED_SKILLS:
        return {
            "success": False,
            "error": (
                f"Unsupported bundled skill '{skill_name}'. "
                f"Available: {', '.join(sorted(_ALLOWED_BUNDLED_SKILLS))}"
            ),
        }

    skill_path = _repo_root() / "my_skills" / normalized / "SKILL.md"
    if not skill_path.exists():
        return {"success": False, "error": f"Bundled MCP skill not found: {normalized}"}
    content = skill_path.read_text(encoding="utf-8")
    return {
        "success": True,
        "name": normalized,
        "path": _normalize_relpath(skill_path.relative_to(_repo_root())),
        "content": content,
    }


def read_bundled_plan_skill() -> Dict[str, Any]:
    """Return the repo-bundled Codex-style ``plan`` skill for MCP guidance."""
    return read_bundled_skill("plan")
