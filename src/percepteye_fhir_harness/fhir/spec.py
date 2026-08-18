"""Load external tools.yaml into ToolSpec objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml


@dataclass
class ToolSpec:
    name: str
    description: str
    kind: Literal["fhir", "write_file"] = "fhir"
    method: str = "GET"
    resource: str = ""
    params: dict[str, str] = field(default_factory=dict)
    fixed_params: dict[str, Any] = field(default_factory=dict)
    default_params: dict[str, Any] = field(default_factory=dict)
    body_fields: dict[str, str] = field(default_factory=dict)
    defaults: dict[str, Any] = field(default_factory=dict)
    properties: dict[str, Any] = field(default_factory=dict)
    required: list[str] = field(default_factory=list)
    default_count: int = 50
    default_page_limit: int = 6

    def openai_schema(self) -> dict[str, Any]:
        props = dict(self.properties)
        if self.kind == "fhir" and self.method.upper() == "GET":
            props.setdefault("count", {
                "type": "integer",
                "description": f"Page size (_count). Default: {self.default_count}",
                "default": self.default_count,
            })
            props.setdefault("page_limit", {
                "type": "integer",
                "description": "Max pages to follow via Bundle.link[rel=next].",
                "default": self.default_page_limit,
            })
        schema: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "parameters": {"type": "object", "properties": props},
        }
        if self.required:
            schema["parameters"]["required"] = self.required
        return schema


def load_tools_file(path: str | Path) -> list[ToolSpec]:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    items = raw.get("tools") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError(f"tools.yaml must contain a tools: list ({path})")
    specs: list[ToolSpec] = []
    for item in items:
        if not isinstance(item, dict) or "name" not in item:
            continue
        props = item.get("properties") or {}
        # If params listed without properties, invent string properties.
        if not props and item.get("params"):
            props = {k: {"type": "string"} for k in item["params"]}
        specs.append(
            ToolSpec(
                name=item["name"],
                description=item.get("description") or item["name"],
                kind=item.get("kind", "write_file" if item["name"] == "write_file" else "fhir"),
                method=str(item.get("method", "GET")).upper(),
                resource=item.get("resource", ""),
                params=dict(item.get("params") or {}),
                fixed_params=dict(item.get("fixed_params") or {}),
                default_params=dict(item.get("default_params") or {}),
                body_fields=dict(item.get("body_fields") or {}),
                defaults=dict(item.get("defaults") or {}),
                properties=props,
                required=list(item.get("required") or []),
                default_count=int(item.get("default_count", 50)),
                default_page_limit=int(item.get("default_page_limit", 6)),
            )
        )
    return specs
