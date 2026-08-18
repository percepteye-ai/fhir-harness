"""Generic FHIR R4 client — Open FHIR or AWS HealthLake (SigV4)."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin

import requests

from ..config import FhirConfig
from .spec import ToolSpec


def _resolve_magic(value: Any) -> Any:
    if value == "$now":
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if value == "$uuid":
        return uuid.uuid4().hex[:8]
    return value


def _set_path(obj: Any, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur: Any = obj
    for i, p in enumerate(parts[:-1]):
        nxt_key = parts[i + 1]
        want_list = nxt_key.isdigit()
        if p.isdigit():
            idx = int(p)
            if not isinstance(cur, list):
                raise TypeError(f"expected list while setting {dotted}")
            while len(cur) <= idx:
                cur.append([] if want_list else {})
            if cur[idx] is None:
                cur[idx] = [] if want_list else {}
            cur = cur[idx]
            continue
        if not isinstance(cur, dict):
            raise TypeError(f"expected dict while setting {dotted}")
        if p not in cur or cur[p] is None:
            cur[p] = [] if want_list else {}
        elif want_list and not isinstance(cur[p], list):
            cur[p] = []
        elif not want_list and not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    last = parts[-1]
    if last.isdigit():
        idx = int(last)
        if not isinstance(cur, list):
            raise TypeError(f"expected list while setting {dotted}")
        while len(cur) <= idx:
            cur.append(None)
        cur[idx] = value
    else:
        cur[last] = value


class FhirClient:
    def __init__(self, cfg: FhirConfig):
        self.cfg = cfg
        self.base = (cfg.base_url or "").rstrip("/") + "/"
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/fhir+json"})
        if cfg.backend == "aws":
            from requests_aws4auth import AWS4Auth

            if not cfg.access_key_id or not cfg.secret_access_key:
                raise ValueError("AWS HealthLake requires access_key_id and secret_access_key")
            self.session.auth = AWS4Auth(
                cfg.access_key_id,
                cfg.secret_access_key,
                cfg.region,
                "healthlake",
            )
        elif cfg.bearer_token:
            self.session.headers["Authorization"] = f"Bearer {cfg.bearer_token}"
        elif cfg.api_key:
            self.session.headers["Authorization"] = f"Api-Key {cfg.api_key}"

    def call(self, spec: ToolSpec, args: dict[str, Any], *, job_dir: Optional[str] = None) -> Any:
        if spec.kind == "write_file":
            from .write_file import write_file

            return write_file(
                args.get("file_path", ""),
                args.get("content", ""),
                args.get("mode", "w"),
                job_dir=job_dir,
            )
        if spec.method == "GET":
            return self._search(spec, args)
        if spec.method == "POST":
            return self._create(spec, args, job_dir=job_dir)
        raise ValueError(f"unsupported method {spec.method} for {spec.name}")

    def _search(self, spec: ToolSpec, args: dict[str, Any]) -> Any:
        query: dict[str, Any] = dict(spec.fixed_params)
        for key, val in spec.default_params.items():
            fhir_name = spec.params.get(key, key)
            query.setdefault(fhir_name, val)
        for arg_name, fhir_name in spec.params.items():
            if arg_name in ("count", "page_limit"):
                continue
            val = args.get(arg_name)
            if val is None or val == "":
                continue
            query[fhir_name] = val
        count = args.get("count", spec.default_count)
        page_limit = int(args.get("page_limit", spec.default_page_limit) or spec.default_page_limit)
        if count is not None:
            query["_count"] = count
        url = urljoin(self.base, spec.resource)
        entries: list[Any] = []
        pages = 0
        while url and pages < page_limit:
            resp = self.session.get(url, params=query if pages == 0 else None, timeout=60)
            resp.raise_for_status()
            bundle = resp.json()
            for e in bundle.get("entry") or []:
                res = e.get("resource", e)
                entries.append(res)
            nxt = None
            for link in bundle.get("link") or []:
                if link.get("relation") == "next":
                    nxt = link.get("url")
                    break
            url = nxt
            pages += 1
            query = {}
        return {
            "resourceType": "Bundle",
            "type": "searchset",
            "total": len(entries),
            "entry": [{"resource": e} for e in entries],
            "entries": entries,
            "pages": pages,
        }

    def _create(self, spec: ToolSpec, args: dict[str, Any], *, job_dir: Optional[str] = None) -> Any:
        body: dict[str, Any] = {"resourceType": spec.resource}
        for path, raw in spec.defaults.items():
            _set_path(body, path, _resolve_magic(raw))
        if spec.body_fields:
            for arg_name, path in spec.body_fields.items():
                if arg_name in args and args[arg_name] not in (None, ""):
                    _set_path(body, path, args[arg_name])
        elif "resource" in args and isinstance(args["resource"], dict):
            body = dict(args["resource"])
            body.setdefault("resourceType", spec.resource)
        else:
            body.update({k: v for k, v in args.items() if v not in (None, "")})

        if self.cfg.virtual_writes:
            body.setdefault("id", f"virtual-{uuid.uuid4().hex[:8]}")
            store = Path(job_dir or os.environ.get("JOB_DIR", "/tmp")) / "virtual_fhir.json"
            existing = []
            if store.exists():
                try:
                    existing = json.loads(store.read_text())
                except Exception:
                    existing = []
            existing.append(body)
            store.write_text(json.dumps(existing, indent=2))
            return body

        url = urljoin(self.base, spec.resource)
        resp = self.session.post(
            url,
            json=body,
            headers={"Content-Type": "application/fhir+json"},
            timeout=60,
        )
        resp.raise_for_status()
        if resp.content:
            return resp.json()
        return {"status": resp.status_code, "ok": True}
