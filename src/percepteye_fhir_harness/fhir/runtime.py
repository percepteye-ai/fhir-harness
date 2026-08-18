"""Bind tools.yaml names as callables (parent process + sandbox kernel)."""

from __future__ import annotations

import os
from typing import Any, Callable

from ..config import FhirConfig
from .client import FhirClient
from .spec import ToolSpec, load_tools_file


def _cfg_from_env() -> FhirConfig:
    backend = os.environ.get("PE_FHIR_BACKEND") or os.environ.get("FHIR_BACKEND") or "open"
    return FhirConfig(
        backend=backend,  # type: ignore[arg-type]
        base_url=os.environ.get("PE_FHIR_BASE_URL")
        or os.environ.get("FHIR_BASE_URL")
        or os.environ.get("HEALTHLAKE_URL")
        or "",
        bearer_token=os.environ.get("PE_FHIR_BEARER_TOKEN"),
        api_key=os.environ.get("PE_FHIR_API_KEY"),
        region=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        virtual_writes=os.environ.get("VIRTUAL_FHIR_MODE", "0") == "1",
        tools_file=os.environ.get("PE_TOOLS_FILE"),
    )


def make_tool_fn(client: FhirClient, spec: ToolSpec) -> Callable[..., Any]:
    positional = list(spec.required) or list(spec.properties.keys())

    def _fn(*args: Any, **kwargs: Any) -> Any:
        merged = dict(kwargs)
        for i, val in enumerate(args):
            if i < len(positional) and positional[i] not in merged:
                merged[positional[i]] = val
        return client.call(spec, merged, job_dir=os.environ.get("JOB_DIR"))

    _fn.__name__ = spec.name
    _fn.__doc__ = spec.description
    return _fn


def bind_tools(namespace: dict[str, Any], *, cfg: FhirConfig | None = None) -> list[ToolSpec]:
    cfg = cfg or _cfg_from_env()
    tools_path = cfg.tools_file or os.environ.get("PE_TOOLS_FILE")
    if not tools_path:
        raise RuntimeError("PE_TOOLS_FILE / fhir.tools_file is required")
    specs = load_tools_file(tools_path)
    client = FhirClient(cfg)
    for spec in specs:
        namespace[spec.name] = make_tool_fn(client, spec)
    return specs
