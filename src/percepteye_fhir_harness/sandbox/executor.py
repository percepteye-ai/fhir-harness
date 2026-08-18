"""Persistent Jupyter kernel sandbox. Injects tools from tools.yaml via runtime.bind_tools."""

from __future__ import annotations

import logging
import os
import queue as _queue
import re as _re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_WRITE_FILE_SENTINEL = "__WRITE_FILE_CALLED__"
_ANSI_RE = _re.compile(r"\x1b\[[0-9;]*[mK]")


@dataclass
class ExecResult:
    status: str
    stdout: str = ""
    stderr: str = ""
    write_file_called: bool = False
    exception_type: Optional[str] = None
    exception_message: Optional[str] = None
    elapsed_s: float = 0.0


@dataclass
class SandboxConfig:
    code_exec_timeout_s: int = 60
    stdout_truncate_bytes: int = 8192
    job_dir: Optional[str] = None
    extra_globals: Dict[str, Any] = field(default_factory=dict)
    env: Dict[str, str] = field(default_factory=dict)


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + f"\n\n[OUTPUT TRUNCATED — {len(text)} chars]\n\n" + text[-half:]


def _build_setup_cell(cfg: SandboxConfig) -> str:
    env_lines = "\n".join(f"_os.environ[{k!r}] = {v!r}" for k, v in cfg.env.items() if v)
    extra_lines = "\n".join(f"{k} = {v!r}" for k, v in (cfg.extra_globals or {}).items())
    return f"""\
import os as _os
import json, re, math, statistics, datetime, collections
{env_lines}
for _var in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS"):
    _os.environ.setdefault(_var, "1")
try:
    import pandas as pd
    import numpy as np
except Exception:
    pass
from percepteye_fhir_harness.fhir.runtime import bind_tools
bind_tools(globals())

_orig_write = globals().get("write_file")
def write_file(path, content, mode="w"):
    result = _orig_write(path, content, mode) if _orig_write else None
    print({_WRITE_FILE_SENTINEL!r})
    return result

{extra_lines}
print("__KERNEL_READY__")
"""


class TaskSandbox:
    def __init__(self, config: SandboxConfig):
        self.cfg = config
        self._km = None
        self._kc = None
        self._start()

    def _start(self) -> None:
        from jupyter_client import KernelManager

        km = KernelManager(kernel_name="python3")
        km.start_kernel()
        kc = km.blocking_client()
        kc.start_channels()
        kc.wait_for_ready(timeout=30)
        self._km = km
        self._kc = kc
        self._inject_setup()

    def _inject_setup(self) -> None:
        res = self._run_cell(_build_setup_cell(self.cfg), timeout_s=30)
        if "__KERNEL_READY__" not in res.stdout:
            logger.warning(
                "Kernel setup may have failed — stdout=%r stderr=%r",
                res.stdout[:200],
                res.stderr[:200],
            )

    @property
    def alive(self) -> bool:
        return self._km is not None and self._km.is_alive()

    def execute(self, code: str, timeout_s: Optional[int] = None) -> ExecResult:
        timeout_s = timeout_s or self.cfg.code_exec_timeout_s
        if not self.alive:
            try:
                self._restart()
            except Exception as exc:
                return ExecResult(
                    status="fatal",
                    stderr=f"kernel dead, restart failed: {exc}",
                    exception_type="KernelDead",
                    exception_message=str(exc),
                )
        res = self._run_cell(code, timeout_s=timeout_s)
        if res.status == "fatal" and not self.alive:
            try:
                self._restart()
            except Exception:
                pass
        return res

    def _restart(self) -> None:
        try:
            if self._kc is not None:
                self._kc.stop_channels()
        except Exception:
            pass
        try:
            if self._km is not None:
                self._km.restart_kernel(now=True)
                time.sleep(0.5)
        except Exception:
            pass
        kc = self._km.blocking_client()
        kc.start_channels()
        kc.wait_for_ready(timeout=30)
        self._kc = kc
        self._inject_setup()

    def _run_cell(self, code: str, timeout_s: int = 60) -> ExecResult:
        kc = self._kc
        start = time.time()
        msg_id = kc.execute(code, silent=False, store_history=False)
        stdout_parts: List[str] = []
        stderr_parts: List[str] = []
        error_name: Optional[str] = None
        error_value: Optional[str] = None
        error_tb: Optional[str] = None
        timed_out = False
        deadline = start + timeout_s
        while True:
            remaining = max(0.05, deadline - time.time())
            try:
                msg = kc.get_iopub_msg(timeout=remaining)
            except _queue.Empty:
                if time.time() >= deadline:
                    timed_out = True
                    break
                continue
            if msg.get("parent_header", {}).get("msg_id") != msg_id:
                continue
            mtype = msg["msg_type"]
            content = msg.get("content", {})
            if mtype == "stream":
                if content.get("name") == "stdout":
                    stdout_parts.append(content.get("text", ""))
                else:
                    stderr_parts.append(content.get("text", ""))
            elif mtype == "error":
                error_name = content.get("ename", "Error")
                error_value = content.get("evalue", "")
                tb_lines = content.get("traceback", [])
                error_tb = "\n".join(_ANSI_RE.sub("", t) for t in tb_lines)
            elif mtype == "status" and content.get("execution_state") == "idle":
                break

        elapsed = time.time() - start
        if timed_out:
            try:
                self._km.interrupt_kernel()
            except Exception:
                pass
            return ExecResult(
                status="timeout",
                stdout=_truncate("".join(stdout_parts), self.cfg.stdout_truncate_bytes),
                stderr=f"cell timeout after {timeout_s:.0f}s",
                exception_type="TimeoutError",
                exception_message=f"per-cell timeout ({timeout_s}s) exceeded",
                elapsed_s=elapsed,
            )

        stdout_raw = "".join(stdout_parts)
        stderr_raw = "".join(stderr_parts)
        if not self.alive:
            return ExecResult(
                status="fatal",
                stdout=_truncate(stdout_raw, self.cfg.stdout_truncate_bytes),
                stderr=_truncate(stderr_raw, self.cfg.stdout_truncate_bytes),
                exception_type="ChildFatal",
                exception_message="kernel process died during execution",
                elapsed_s=elapsed,
            )

        write_file_called = False
        if _WRITE_FILE_SENTINEL in stdout_raw:
            write_file_called = True
            stdout_raw = stdout_raw.replace(_WRITE_FILE_SENTINEL + "\n", "").replace(
                _WRITE_FILE_SENTINEL, ""
            )

        if error_name:
            return ExecResult(
                status="exception",
                stdout=_truncate(stdout_raw, self.cfg.stdout_truncate_bytes),
                stderr=_truncate((stderr_raw + ("\n" + (error_tb or ""))).strip(), self.cfg.stdout_truncate_bytes),
                write_file_called=write_file_called,
                exception_type=error_name,
                exception_message=error_value,
                elapsed_s=elapsed,
            )
        return ExecResult(
            status="ok",
            stdout=_truncate(stdout_raw, self.cfg.stdout_truncate_bytes),
            stderr=_truncate(stderr_raw, self.cfg.stdout_truncate_bytes),
            write_file_called=write_file_called,
            elapsed_s=elapsed,
        )

    def close(self) -> None:
        try:
            if self._kc is not None:
                self._kc.stop_channels()
        except Exception:
            pass
        try:
            if self._km is not None:
                self._km.shutdown_kernel(now=True)
        except Exception:
            pass
        self._km = None
        self._kc = None

    def __enter__(self) -> "TaskSandbox":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
