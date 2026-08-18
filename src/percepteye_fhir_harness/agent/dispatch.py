"""Dispatch OpenAI tool calls to sandbox or FHIR client."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from ..config import FhirConfig
from ..fhir.client import FhirClient
from ..fhir.spec import ToolSpec
from ..sandbox.executor import ExecResult, TaskSandbox
from .registry import AgentMode

logger = logging.getLogger(__name__)

WRITE_FILE_DISPATCH_SENTINEL = "__WRITE_FILE_COMPLETE__"


def write_trajectory_entry(job_dir: Path, tool_name: str, input_args: dict, output_str: str) -> None:
    try:
        log_dir = job_dir / "logs" / "agent"
        log_dir.mkdir(parents=True, exist_ok=True)
        entry = json.dumps(
            {
                "type": "tool_call",
                "metadata": {
                    "tool_name": tool_name,
                    "input": input_args,
                    "output": output_str[:8000],
                },
            },
            default=str,
        )
        with open(log_dir / "trajectory.log", "a") as fh:
            fh.write(entry + "\n")
    except Exception as exc:
        logger.warning("Failed to write trajectory entry for %s: %s", tool_name, exc)


def _format_exec_result(res: ExecResult, max_chars: int = 6000) -> str:
    parts = []
    if res.status != "ok":
        parts.append(f"status={res.status}")
    if res.exception_type:
        parts.append(f"exception: {res.exception_type}: {res.exception_message}")
    if res.stdout:
        s = res.stdout
        if len(s) > max_chars:
            half = max_chars // 2
            s = (
                s[:half]
                + f"\n\n[OUTPUT TRUNCATED — {len(s)} chars total]\n\n"
                + s[-half:]
            )
        parts.append(s)
    if res.stderr and res.status != "ok":
        parts.append("stderr:\n" + res.stderr[: max_chars // 2])
    return "\n".join(parts) if parts else "(no output)"


def dispatch(
    tool_name: str,
    args: dict[str, Any],
    *,
    sandbox: Optional[TaskSandbox],
    job_dir: Path,
    fhir_cfg: FhirConfig,
    specs: list[ToolSpec],
    agent_mode: AgentMode = AgentMode.CODE_EXEC_ONLY,
    max_tool_result_chars: int = 6000,
) -> str:
    spec_by_name = {s.name: s for s in specs}
    is_fhir_or_file = tool_name != "code_exec"
    if agent_mode == AgentMode.CODE_EXEC_ONLY and is_fhir_or_file and tool_name != "write_file":
        args_repr = ", ".join(f"{k}={v!r}" for k, v in list(args.items())[:3])
        return json.dumps({
            "error": (
                f"Direct tool call not allowed in code_exec_only mode. "
                f"Use code_exec instead:\n"
                f"  result = {tool_name}({args_repr})\n"
                f"  print(json.dumps(result, default=str))"
            )
        })

    if tool_name == "code_exec":
        if sandbox is None:
            return json.dumps({"error": "code_exec unavailable in tools_only mode"})
        code = args.get("code", "")
        if not str(code).strip():
            return json.dumps({"error": "code argument is empty"})
        res = sandbox.execute(code)
        result = _format_exec_result(res, max_chars=max_tool_result_chars)
        if res.write_file_called:
            result = result + "\n" + WRITE_FILE_DISPATCH_SENTINEL
        return result

    spec = spec_by_name.get(tool_name)
    if spec is None:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    try:
        client = FhirClient(fhir_cfg)
        result = client.call(spec, args, job_dir=str(job_dir))
        result_str = json.dumps(result, default=str)
        write_trajectory_entry(job_dir, tool_name, args, result_str[:8000])
        if len(result_str) > 10_000:
            result_str = (
                result_str[:10_000]
                + f"\n\n[OUTPUT TRUNCATED — showing first 10000 of {len(result_str)} chars.]"
            )
        return result_str
    except Exception as exc:
        return json.dumps({"error": str(exc)})
