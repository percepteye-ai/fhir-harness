# PerceptEye FHIR Harness

Installable library for a clinical agent loop against your FHIR server (Open FHIR R4 or AWS HealthLake). FastAPI + Playground UI call the same library.

## Install

From the repo root, with a Python 3.10+ venv:

```bash
pip install -e .
pip install -e '.[tinker]'   # needed for model.backend: tinker
cp .env.example .env         # fill TINKER_API_KEY and AWS_* ; do not commit .env
set -a && source .env && set +a
```

Defaults in `examples/rollout.yaml` and `.env.example` are the tested path: Tinker SamplingClient (ckpt 0077) + HealthLake. Auth keys stay in `.env` only.

## Library

```python
from percepteye_fhir_harness import load_config, run_rollout

cfg = load_config("examples/rollout.yaml")
async for event in run_rollout(cfg):
    print(event["type"])
```

`run_rollout` uses `cfg.resolved_instruction()` from `examples/instruction.md` unless you pass `instruction=`. Pass `system_prompt=` to override the `code_exec_only` default. Events include `start`, `assistant`, `code`, `tool_result`, `terminated`, `done`.

Need the config in memory:

```python
cfg = load_config("examples/rollout.yaml")
# cfg.model.backend, cfg.fhir.backend, cfg.rollout.agent_mode, ...
```

## CLI

```bash
set -a && source .env && set +a
pe-harness --config examples/rollout.yaml
pe-harness --config examples/rollout.yaml --instruction "…" --system-prompt "…" -v
```

## Config

| File | Role |
|---|---|
| `examples/rollout.yaml` | Model, FHIR, sampling, instruction path |
| `examples/instruction.md` | Default user prompt (Playground + CLI) |
| `examples/tools.yaml` | 13 FHIR tools + `write_file` |
| `.env` | Keys and env overrides (`PE_*`, `TINKER_API_KEY`, `AWS_*`) |

YAML keys that are **present** (including `""`) win over env. Omit a key in YAML if you want the env default. Tools: add a resource in `tools.yaml`, not in Python.

### Model backends

- **`tinker`** (example default) — Tinker `SamplingClient` + `qwen3_5` renderer. Needs `tinker>=0.23`, `PE_TINKER_SAMPLER_PATH` / `model.sampler_path`, and `TINKER_API_KEY`. Optional: `PE_TINKER_BASE_MODEL`, `PE_TINKER_RENDERER`. If `enable_thinking` is false and renderer is `qwen3_5`, the harness uses `qwen3_5_disable_thinking`. Sampling: `temperature`, `top_p`, `top_k`, `max_response_tokens`. `presence_penalty` / `min_p` apply to `openai` only.
- **`openai`** — hosted OpenAI-compatible `/v1` (`PE_LLM_BASE_URL`, `PE_LLM_MODEL`, `PE_LLM_API_KEY`). Some hosts leave Qwen 3.5 tool XML in `content` instead of `tool_calls`.

### FHIR

- **`aws`** (example default) — HealthLake SigV4. `fhir.base_url` is the datastore R4 root. Keys: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`.
- **`open`** — Open FHIR R4. Optional `PE_FHIR_BEARER_TOKEN` / `PE_FHIR_API_KEY`.
- `virtual_writes: true` keeps POSTs local (no writes to the live server).

## Launch backend

From the **repo root**, with `.env` sourced (so Tinker and AWS keys are in the process):

```bash
cd /path/to/percepteye-fhir-harness
set -a && source .env && set +a
# optional: PE_CONFIG=/path/to/rollout.yaml  (default is examples/rollout.yaml)
uvicorn services.backend.app:app --host 0.0.0.0 --port 8010
```

Check: `curl http://localhost:8010/health`  
Playground API: `GET /api/prompts`, `POST /api/playground/run` (SSE).

## Launch UI

In a second terminal:

```bash
cd /path/to/percepteye-fhir-harness/services/frontend
npm install
NEXT_PUBLIC_API_BASE=http://localhost:8010 npm run dev -- --hostname 0.0.0.0 --port 3000
```

Open **http://localhost:3000** (prefer `localhost` over `127.0.0.1` in Next.js dev). System and user prompts load from `/api/prompts` (package system prompt + `examples/instruction.md`). Edit them and click **Run**. You can paste a tools YAML override per request.
