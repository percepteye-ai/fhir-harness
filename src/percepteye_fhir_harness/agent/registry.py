"""OpenAI tool schemas from tools.yaml + code_exec."""

from __future__ import annotations

from enum import Enum
from typing import Any

from ..fhir.spec import ToolSpec


class AgentMode(str, Enum):
    TOOLS_ONLY = "tools_only"
    HYBRID = "hybrid"
    CODE_EXEC_ONLY = "code_exec_only"


CODE_EXEC_SCHEMA: dict[str, Any] = {
    "name": "code_exec",
    "description": (
        "Execute Python in a persistent sandbox. FHIR tools and write_file "
        "are available as globals. Print compact summaries; store large results in variables."
    ),
    "parameters": {
        "type": "object",
        "properties": {"code": {"type": "string", "description": "Python source to run"}},
        "required": ["code"],
    },
}


def openai_tools(mode: AgentMode, specs: list[ToolSpec]) -> list[dict[str, Any]]:
    fhir_and_file = [{"type": "function", "function": s.openai_schema()} for s in specs]
    code = [{"type": "function", "function": CODE_EXEC_SCHEMA}]
    if mode == AgentMode.TOOLS_ONLY:
        return fhir_and_file
    if mode == AgentMode.CODE_EXEC_ONLY:
        # Model still sees FHIR schemas so it knows the Python API inside the sandbox.
        return code + fhir_and_file
    return fhir_and_file + code
