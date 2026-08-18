"""Local write_file — not a FHIR call."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional


def write_file(
    file_path: str,
    content: str,
    mode: str = "w",
    job_dir: Optional[str] = None,
) -> dict[str, Any]:
    virtual = "/workspace/output"
    root = job_dir or os.environ.get("JOB_DIR", "")
    if file_path.startswith(virtual) and root:
        file_path = str(Path(root) / "workspace" / "output") + file_path[len(virtual) :]
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, mode, encoding="utf-8") as fh:
        fh.write(content if isinstance(content, str) else str(content))
    return {"ok": True, "path": str(path), "bytes": path.stat().st_size}
