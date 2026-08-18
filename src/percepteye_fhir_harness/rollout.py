"""Streaming clinical agent rollout."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from openai import AsyncOpenAI

from .agent.dispatch import WRITE_FILE_DISPATCH_SENTINEL, dispatch
from .agent.registry import AgentMode, openai_tools
from .config import FhirConfig, HarnessConfig
from .fhir.spec import load_tools_file
from .prompts import default_system_prompt
from .sandbox.executor import SandboxConfig, TaskSandbox

logger = logging.getLogger(__name__)


def _choice_to_dict(choice: Any) -> Dict[str, Any]:
    msg = choice.message
    d: Dict[str, Any] = {"role": msg.role or "assistant"}
    if msg.content:
        d["content"] = msg.content
    if msg.tool_calls:
        d["tool_calls"] = [tc.model_dump() for tc in msg.tool_calls]
    if not d.get("content") and not d.get("tool_calls"):
        d["content"] = ""
    return d


def _to_api_messages(messages_and_choices: List[Any]) -> List[Dict[str, Any]]:
    result = []
    for msg in messages_and_choices:
        if hasattr(msg, "finish_reason"):
            result.append(_choice_to_dict(msg))
        else:
            result.append(msg)
    return result


def _extract_thinking(msg: Any) -> str:
    return (
        getattr(msg, "reasoning_content", None)
        or (getattr(msg, "model_extra", None) or {}).get("reasoning_content", "")
        or ""
    )


def _fhir_env(fhir: FhirConfig, tools_file: Path, job_dir: Path) -> dict[str, str]:
    env = {
        "PE_FHIR_BACKEND": fhir.backend,
        "FHIR_BACKEND": "healthlake" if fhir.backend == "aws" else "open",
        "PE_FHIR_BASE_URL": fhir.base_url,
        "FHIR_BASE_URL": fhir.base_url,
        "PE_TOOLS_FILE": str(tools_file),
        "JOB_DIR": str(job_dir),
        "VIRTUAL_FHIR_MODE": "1" if fhir.virtual_writes else "0",
        "AWS_DEFAULT_REGION": fhir.region,
    }
    if fhir.backend == "aws":
        env["HEALTHLAKE_URL"] = fhir.base_url
        if fhir.access_key_id:
            env["AWS_ACCESS_KEY_ID"] = fhir.access_key_id
        if fhir.secret_access_key:
            env["AWS_SECRET_ACCESS_KEY"] = fhir.secret_access_key
    if fhir.bearer_token:
        env["PE_FHIR_BEARER_TOKEN"] = fhir.bearer_token
    if fhir.api_key:
        env["PE_FHIR_API_KEY"] = fhir.api_key
    return {k: v for k, v in env.items() if v}


async def run_rollout(
    cfg: HarnessConfig,
    *,
    instruction: Optional[str] = None,
    system_prompt: Optional[str] = None,
    openai_client: Optional[Any] = None,
    rollout_id: Optional[str] = None,
) -> AsyncIterator[Dict[str, Any]]:
    r = cfg.rollout
    user = (instruction if instruction is not None else cfg.resolved_instruction()).strip()
    if not user:
        yield {"type": "error", "message": "instruction is required"}
        return

    sys_prompt = system_prompt if system_prompt is not None else cfg.resolved_system_prompt()
    if not sys_prompt:
        sys_prompt = default_system_prompt(r.agent_mode, r.max_tool_result_chars)

    tools_path = cfg.resolved_tools_file()
    specs = load_tools_file(tools_path)
    agent_mode = AgentMode(r.agent_mode)
    tools = openai_tools(agent_mode, specs)

    rid = rollout_id or f"run-{uuid.uuid4().hex[:8]}"
    job_dir = Path(r.job_dir) if r.job_dir else Path.cwd() / "jobs" / rid
    job_dir.mkdir(parents=True, exist_ok=True)
    for key, val in _fhir_env(cfg.fhir, tools_path, job_dir).items():
        os.environ[key] = val

    if openai_client is not None:
        client = openai_client
        model_name = cfg.model.model or cfg.model.sampler_path
    elif cfg.model.backend == "tinker":
        from .llm.tinker_sampling import TinkerSamplingChatClient

        client = TinkerSamplingChatClient.from_config(cfg)
        model_name = cfg.model.sampler_path or cfg.model.base_model
    else:
        client = AsyncOpenAI(base_url=cfg.model.base_url, api_key=cfg.model.api_key or "EMPTY")
        model_name = cfg.model.model

    messages_list: List[Any] = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user},
    ]

    yield {
        "type": "start",
        "rollout_id": rid,
        "agent_mode": agent_mode.value,
        "model": model_name,
        "max_turns": r.max_main_turns,
    }
    yield {"type": "system_prompt", "system_prompt": sys_prompt, "instruction": user}

    t0 = time.monotonic()
    terminating_reason = "max_turns"
    n_turns = 0
    sandbox: Optional[TaskSandbox] = None
    if agent_mode != AgentMode.TOOLS_ONLY:
        sbox_cfg = SandboxConfig(
            code_exec_timeout_s=r.code_exec_timeout_s,
            stdout_truncate_bytes=r.stdout_truncate_bytes,
            job_dir=str(job_dir),
            env=_fhir_env(cfg.fhir, tools_path, job_dir),
        )
        sandbox = await asyncio.to_thread(lambda: TaskSandbox(sbox_cfg).__enter__())

    try:
        for turn_idx in range(r.max_main_turns):
            if time.monotonic() - t0 > r.wallclock_cap_s:
                terminating_reason = "wallclock"
                break
            try:
                extra: Dict[str, Any] = {}
                if r.top_k and r.top_k > 0:
                    extra.setdefault("extra_body", {})["top_k"] = r.top_k
                if r.min_p > 0:
                    extra.setdefault("extra_body", {})["min_p"] = r.min_p
                if not r.enable_thinking:
                    extra.setdefault("extra_body", {}).setdefault("chat_template_kwargs", {})[
                        "enable_thinking"
                    ] = False
                completion = await client.chat.completions.create(
                    model=model_name,
                    messages=_to_api_messages(messages_list),
                    tools=tools,
                    tool_choice="auto",
                    temperature=r.temperature,
                    top_p=r.top_p,
                    presence_penalty=r.presence_penalty,
                    max_tokens=r.max_response_tokens,
                    **extra,
                )
            except Exception as exc:
                terminating_reason = "fatal"
                yield {"type": "error", "turn": turn_idx, "message": f"LLM call failed: {exc}"}
                break

            choice = completion.choices[0]
            messages_list.append(choice)
            msg = choice.message
            tool_calls = msg.tool_calls or []
            n_turns = turn_idx + 1

            if not tool_calls:
                yield {
                    "type": "assistant",
                    "turn": turn_idx,
                    "text": msg.content or "",
                    "thinking": _extract_thinking(msg),
                    "tool_calls": [],
                    "finish_reason": choice.finish_reason,
                }
                terminating_reason = "final_answer"
                break

            tc_summaries = []
            for tc in tool_calls:
                try:
                    _a = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    _a = {}
                tc_summaries.append(f"{tc.function.name}({list(_a.keys())})")
            yield {
                "type": "assistant",
                "turn": turn_idx,
                "text": msg.content or "",
                "thinking": _extract_thinking(msg),
                "tool_calls": tc_summaries,
                "finish_reason": choice.finish_reason,
            }

            first_tool = True
            write_file_detected = False
            for tc in tool_calls:
                tool_name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                if tool_name == "code_exec":
                    yield {"type": "code", "turn": turn_idx, "code": args.get("code", "")}
                result_str_full = await asyncio.to_thread(
                    dispatch,
                    tool_name,
                    args,
                    sandbox=sandbox,
                    job_dir=job_dir,
                    fhir_cfg=cfg.fhir,
                    specs=specs,
                    agent_mode=agent_mode,
                    max_tool_result_chars=r.max_tool_result_chars,
                )
                if tool_name == "code_exec" and WRITE_FILE_DISPATCH_SENTINEL in result_str_full:
                    write_file_detected = True
                    result_str_full = result_str_full.replace("\n" + WRITE_FILE_DISPATCH_SENTINEL, "").replace(
                        WRITE_FILE_DISPATCH_SENTINEL, ""
                    )
                if first_tool:
                    result_str_full = r.turn_hint.format(turn=turn_idx + 1, max=r.max_main_turns) + result_str_full
                    first_tool = False
                cap = r.max_tool_result_chars
                result_str_hist = (
                    result_str_full[:cap] + f"\n\n[OUTPUT TRUNCATED — {len(result_str_full)} chars]"
                    if len(result_str_full) > cap
                    else result_str_full
                )
                messages_list.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str_hist,
                })
                yield {"type": "tool_result", "turn": turn_idx, "tool": tool_name, "result": result_str_full}

            if write_file_detected:
                terminating_reason = "write_file_complete"
                break
            if turn_idx == r.max_main_turns - 2:
                penultimate = r.final_turn_hint.format(turn=turn_idx + 1, max=r.max_main_turns)
                messages_list.append({"role": "user", "content": penultimate})
                yield {"type": "nudge", "turn": turn_idx, "message": penultimate}
    finally:
        if sandbox is not None:
            try:
                await asyncio.to_thread(lambda: sandbox.__exit__(None, None, None))
            except Exception:
                pass

    yield {"type": "terminated", "reason": terminating_reason, "turns": n_turns}
    yield {"type": "done", "rollout_id": rid, "terminating_reason": terminating_reason}
