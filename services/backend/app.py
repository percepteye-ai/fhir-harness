"""FastAPI for PerceptEye FHIR Harness — health + live Playground SSE.

Every POST /api/playground/run can override system_prompt and instruction.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from percepteye_fhir_harness import load_config, run_rollout
from percepteye_fhir_harness.prompts import default_system_prompt

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pe_harness")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = os.environ.get("PE_CONFIG", str(REPO_ROOT / "examples" / "rollout.yaml"))

_run_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("PerceptEye FHIR Harness backend starting. config=%s", DEFAULT_CONFIG)
    yield


app = FastAPI(title="PerceptEye FHIR Harness", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("PE_CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _base_config():
    path = Path(DEFAULT_CONFIG)
    return load_config(path if path.exists() else None)


class PlaygroundRequest(BaseModel):
    instruction: str = ""
    system_prompt: Optional[str] = None
    agent_mode: Optional[Literal["code_exec_only", "hybrid", "tools_only"]] = None
    tools_file: Optional[str] = None
    tools_yaml: Optional[str] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    presence_penalty: Optional[float] = None
    max_response_tokens: Optional[int] = None
    max_main_turns: Optional[int] = None
    enable_thinking: Optional[bool] = None
    virtual_writes: Optional[bool] = None


def _apply_request(cfg, req: PlaygroundRequest, tmp_tools: Optional[str]) -> None:
    if req.agent_mode:
        cfg.rollout.agent_mode = req.agent_mode
    if req.temperature is not None:
        cfg.rollout.temperature = req.temperature
    if req.top_p is not None:
        cfg.rollout.top_p = req.top_p
    if req.top_k is not None:
        cfg.rollout.top_k = req.top_k
    if req.presence_penalty is not None:
        cfg.rollout.presence_penalty = req.presence_penalty
    if req.max_response_tokens is not None:
        cfg.rollout.max_response_tokens = req.max_response_tokens
    if req.max_main_turns is not None:
        cfg.rollout.max_main_turns = req.max_main_turns
    if req.enable_thinking is not None:
        cfg.rollout.enable_thinking = req.enable_thinking
    if req.virtual_writes is not None:
        cfg.fhir.virtual_writes = req.virtual_writes
    if tmp_tools:
        cfg.fhir.tools_file = tmp_tools
    elif req.tools_file:
        cfg.fhir.tools_file = req.tools_file


def _health_payload() -> Dict[str, Any]:
    cfg = _base_config()
    tools_path = cfg.resolved_tools_file()
    return {
        "status": "ok",
        "service": "percepteye-fhir-harness",
        "llm_backend": cfg.model.backend,
        "model_configured": bool(
            (cfg.model.backend == "tinker" and cfg.model.sampler_path)
            or (cfg.model.base_url and cfg.model.model)
        ),
        "model": cfg.model.sampler_path if cfg.model.backend == "tinker" else (cfg.model.model or None),
        "fhir_backend": cfg.fhir.backend,
        "fhir_configured": bool(cfg.fhir.base_url),
        "tools_file": str(tools_path) if tools_path.exists() else None,
        "agent_mode": cfg.rollout.agent_mode,
    }


@app.get("/health")
@app.get("/api/health")
async def health() -> Dict[str, Any]:
    return _health_payload()


@app.get("/api/prompts")
async def prompts(agent_mode: str = "code_exec_only") -> Dict[str, Any]:
    cfg = _base_config()
    mode = agent_mode or cfg.rollout.agent_mode
    return {
        "agent_mode": mode,
        "system_prompt": cfg.resolved_system_prompt()
        or default_system_prompt(mode, cfg.rollout.max_tool_result_chars),
        "instruction": cfg.resolved_instruction(),
    }


@app.post("/playground/run")
@app.post("/api/playground/run")
async def playground_run(req: PlaygroundRequest):
    tmp_path: Optional[str] = None
    if req.tools_yaml and req.tools_yaml.strip():
        fd, tmp_path = tempfile.mkstemp(prefix="pe-tools-", suffix=".yaml")
        os.close(fd)
        Path(tmp_path).write_text(req.tools_yaml)

    async def event_stream():
        if _run_lock.locked():
            yield _sse({"type": "queued", "message": "another run is in progress; waiting…"})
        async with _run_lock:
            try:
                cfg = _base_config()
                _apply_request(cfg, req, tmp_path)
                instruction = (req.instruction or "").strip() or cfg.resolved_instruction()
                system_prompt = req.system_prompt
                if system_prompt is None:
                    system_prompt = cfg.resolved_system_prompt()
                if not system_prompt:
                    system_prompt = default_system_prompt(
                        cfg.rollout.agent_mode, cfg.rollout.max_tool_result_chars
                    )
                yield _sse({"type": "preparing", "message": "starting rollout…"})
                async for event in run_rollout(
                    cfg,
                    instruction=instruction,
                    system_prompt=system_prompt,
                    rollout_id=f"playground-{uuid.uuid4().hex[:8]}",
                ):
                    yield _sse(event)
            except Exception as exc:  # noqa: BLE001
                logger.exception("playground run failed")
                yield _sse({"type": "error", "message": str(exc)})
            finally:
                if tmp_path:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("services.backend.app:app", host="0.0.0.0", port=8010, reload=False)
