#!/usr/bin/env python3
"""Bundled Hermes /plan skill helpers for MCP clients."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _normalize_relpath(path: Path) -> str:
    return path.as_posix()


def read_bundled_plan_skill() -> Dict[str, Any]:
    """Return the repo-bundled Codex-style ``plan`` skill for MCP guidance."""
    skill_path = _repo_root() / "my_skills" / "plan" / "SKILL.md"
    if not skill_path.exists():
        return {"success": False, "error": "Bundled MCP plan skill not found."}
    content = skill_path.read_text(encoding="utf-8")
    return {
        "success": True,
        "name": "plan",
        "path": _normalize_relpath(skill_path.relative_to(_repo_root())),
        "content": content,
    }
