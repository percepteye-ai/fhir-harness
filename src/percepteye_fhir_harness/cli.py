"""pe-harness — run one rollout from a YAML config."""

from __future__ import annotations

import argparse
import asyncio
import json

from .config import load_config
from .rollout import run_rollout


async def _run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    if args.instruction:
        cfg.rollout.instruction = args.instruction
    if args.system_prompt:
        cfg.rollout.system_prompt = args.system_prompt
    async for event in run_rollout(cfg):
        et = event.get("type")
        if args.verbose or et in ("error", "terminated", "done", "assistant"):
            print(json.dumps(event, default=str)[:4000], flush=True)
        if et == "error":
            return 1
    return 0


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="pe-harness", description="PerceptEye FHIR Harness rollout")
    p.add_argument("--config", required=True, help="Path to rollout.yaml")
    p.add_argument("--instruction", help="Override user prompt")
    p.add_argument("--system-prompt", dest="system_prompt", help="Override system prompt")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
