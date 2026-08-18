"""Load default system templates from package data."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path


def default_system_prompt(agent_mode: str, max_tool_result_chars: int = 6000) -> str:
    name = {
        "code_exec_only": "code_exec_only.md",
        "hybrid": "hybrid.md",
        "tools_only": "tools_only.md",
    }.get(agent_mode, "code_exec_only.md")
    try:
        text = (files("percepteye_fhir_harness.prompts") / name).read_text(encoding="utf-8")
    except Exception:
        text = Path(__file__).parent.joinpath(name).read_text(encoding="utf-8")
    return text.format(max_tool_result_chars=max_tool_result_chars)
