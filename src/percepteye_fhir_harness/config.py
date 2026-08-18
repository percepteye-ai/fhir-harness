"""YAML / env configuration for model, FHIR, and rollout."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator


class ModelConfig(BaseModel):
    backend: Literal["openai", "tinker"] = Field(
        default_factory=lambda: os.environ.get("PE_LLM_BACKEND", "openai")  # type: ignore[arg-type]
    )
    base_url: str = Field(default_factory=lambda: os.environ.get("PE_LLM_BASE_URL", ""))
    model: str = Field(default_factory=lambda: os.environ.get("PE_LLM_MODEL", ""))
    api_key: str = Field(default_factory=lambda: os.environ.get("PE_LLM_API_KEY", "EMPTY"))
    sampler_path: str = Field(default_factory=lambda: os.environ.get("PE_TINKER_SAMPLER_PATH", ""))
    tinker_api_key: str = Field(
        default_factory=lambda: os.environ.get("TINKER_API_KEY")
        or os.environ.get("PE_TINKER_API_KEY", "")
    )
    base_model: str = Field(
        default_factory=lambda: os.environ.get("PE_TINKER_BASE_MODEL", "Qwen/Qwen3.6-35B-A3B")
    )
    renderer: str = Field(default_factory=lambda: os.environ.get("PE_TINKER_RENDERER", "qwen3_5"))

    @field_validator("backend", mode="before")
    @classmethod
    def _normalize_llm_backend(cls, v: Any) -> str:
        if v in (None, ""):
            return "openai"
        return str(v).lower()

    def resolved_renderer(self, enable_thinking: bool) -> str:
        name = (self.renderer or "qwen3_5").strip()
        if not enable_thinking and name == "qwen3_5":
            return "qwen3_5_disable_thinking"
        return name

    def resolved_tinker_api_key(self) -> str:
        return self.tinker_api_key or os.environ.get("TINKER_API_KEY") or ""


class FhirConfig(BaseModel):
    backend: Literal["open", "aws"] = Field(
        default_factory=lambda: os.environ.get("PE_FHIR_BACKEND", "open")  # type: ignore[arg-type]
    )
    base_url: str = Field(default_factory=lambda: os.environ.get("PE_FHIR_BASE_URL", ""))
    bearer_token: Optional[str] = Field(default_factory=lambda: os.environ.get("PE_FHIR_BEARER_TOKEN"))
    api_key: Optional[str] = Field(default_factory=lambda: os.environ.get("PE_FHIR_API_KEY"))
    region: str = Field(default_factory=lambda: os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
    access_key_id: Optional[str] = Field(default_factory=lambda: os.environ.get("AWS_ACCESS_KEY_ID"))
    secret_access_key: Optional[str] = Field(default_factory=lambda: os.environ.get("AWS_SECRET_ACCESS_KEY"))
    virtual_writes: bool = False
    tools_file: Optional[str] = None

    @field_validator("backend", mode="before")
    @classmethod
    def _normalize_backend(cls, v: Any) -> str:
        if v in (None, ""):
            return "open"
        raw = str(v).lower()
        if raw in ("aws", "healthlake"):
            return "aws"
        return raw


class RolloutConfig(BaseModel):
    instruction: str = ""
    instruction_file: Optional[str] = None
    system_prompt: Optional[str] = None
    system_prompt_file: Optional[str] = None
    agent_mode: Literal["code_exec_only", "hybrid", "tools_only"] = "code_exec_only"
    max_main_turns: int = 30
    wallclock_cap_s: int = 600
    code_exec_timeout_s: int = 60
    max_tool_result_chars: int = 6000
    stdout_truncate_bytes: int = 8192
    enable_thinking: bool = True
    temperature: float = 1.0
    top_p: float = 0.8
    top_k: int = 20
    min_p: float = 0.0
    presence_penalty: float = 1.5
    max_response_tokens: int = 4096
    job_dir: Optional[str] = None
    turn_hint: str = "[Turn {turn}/{max}]\n\n"
    final_turn_hint: str = (
        "[Turn {turn}/{max} — NEXT IS YOUR FINAL TURN. "
        "On your next response you MUST call write_file to submit your answer, "
        "even if your analysis is incomplete.]"
    )


class HarnessConfig(BaseModel):
    model: ModelConfig = Field(default_factory=ModelConfig)
    fhir: FhirConfig = Field(default_factory=FhirConfig)
    rollout: RolloutConfig = Field(default_factory=RolloutConfig)

    def resolved_instruction(self) -> str:
        if self.rollout.instruction.strip():
            return self.rollout.instruction
        if self.rollout.instruction_file:
            return Path(self.rollout.instruction_file).read_text()
        return ""

    def resolved_system_prompt(self) -> Optional[str]:
        if self.rollout.system_prompt and self.rollout.system_prompt.strip():
            return self.rollout.system_prompt
        if self.rollout.system_prompt_file:
            return Path(self.rollout.system_prompt_file).read_text()
        return None

    def resolved_tools_file(self) -> Path:
        raw = self.fhir.tools_file or os.environ.get("PE_TOOLS_FILE")
        if raw:
            return Path(raw).expanduser().resolve()
        here = Path(__file__).resolve().parents[2]
        return here / "examples" / "tools.yaml"


def load_config(path: str | Path | None = None, overrides: Optional[dict[str, Any]] = None) -> HarnessConfig:
    data: dict[str, Any] = {}
    if path:
        raw = yaml.safe_load(Path(path).read_text()) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"config must be a mapping: {path}")
        data = raw
    if overrides:
        data = _deep_merge(data, overrides)
    cfg = HarnessConfig.model_validate(data)
    if path:
        cfg_dir = Path(path).parent
        for attr, owner in (
            ("tools_file", cfg.fhir),
            ("instruction_file", cfg.rollout),
            ("system_prompt_file", cfg.rollout),
        ):
            raw = getattr(owner, attr, None)
            if raw and not Path(raw).is_absolute():
                sibling = cfg_dir / raw
                if sibling.exists():
                    setattr(owner, attr, str(sibling.resolve()))
    return cfg


def _deep_merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out
