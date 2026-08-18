"""OpenAI-shaped chat client via Tinker SamplingClient + cookbook renderer.

Encode with the HF chat template (tools + enable_thinking), sample with
``SamplingParams``, decode with ``qwen3_5`` ``parse_response`` → ``to_openai_message``
so ``message.tool_calls`` is native OpenAI, not XML-in-content.
"""

from __future__ import annotations

import json
import time
import uuid
from types import SimpleNamespace
from typing import Any, Optional

from openai.types.chat.chat_completion import ChatCompletion, Choice
from openai.types.chat.chat_completion_message import ChatCompletionMessage
from openai.types.chat.chat_completion_message_function_tool_call import (
    ChatCompletionMessageFunctionToolCall,
    Function,
)
from openai.types.completion_usage import CompletionUsage

from ..config import HarnessConfig


def _require_tinker():
    try:
        import tinker
        from tinker_cookbook import renderers
        from tinker_cookbook.tokenizer_utils import get_tokenizer
    except ImportError as exc:
        raise ImportError(
            "model.backend=tinker requires the optional extra: pip install -e '.[tinker]'"
        ) from exc
    return tinker, renderers, get_tokenizer


def _normalize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """HF Qwen3.5 templates iterate tool arguments as dicts, not JSON strings."""
    out: list[dict[str, Any]] = []
    for msg in messages:
        m = dict(msg)
        if m.get("content") is None:
            m["content"] = ""
        tool_calls = m.get("tool_calls")
        if not isinstance(tool_calls, list):
            out.append(m)
            continue
        normalized: list[Any] = []
        for tc in tool_calls:
            if not isinstance(tc, dict):
                normalized.append(tc)
                continue
            fn = tc.get("function")
            if not isinstance(fn, dict):
                normalized.append(tc)
                continue
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    parsed = json.loads(args)
                except json.JSONDecodeError:
                    normalized.append(tc)
                    continue
                if isinstance(parsed, dict):
                    normalized.append({**tc, "function": {**fn, "arguments": parsed}})
                    continue
            normalized.append(tc)
        m["tool_calls"] = normalized
        out.append(m)
    return out


def _finish_reason(stop_reason: str, has_tools: bool) -> str:
    if has_tools:
        return "tool_calls"
    if stop_reason == "length":
        return "length"
    return "stop"


def _tool_calls_from_openai_message(openai_message: dict[str, Any]) -> Optional[list[ChatCompletionMessageFunctionToolCall]]:
    raw = openai_message.get("tool_calls") or []
    if not raw:
        return None
    out: list[ChatCompletionMessageFunctionToolCall] = []
    for tc in raw:
        args = tc["function"]["arguments"]
        if not isinstance(args, str):
            args = json.dumps(args)
        out.append(
            ChatCompletionMessageFunctionToolCall(
                type="function",
                id=tc.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                function=Function(name=tc["function"]["name"], arguments=args),
            )
        )
    return out


class TinkerSamplingChatClient:
    """Thin object with ``chat.completions.create(...)`` matching AsyncOpenAI."""

    def __init__(
        self,
        *,
        sampler_path: str,
        api_key: str,
        base_model: str,
        renderer_name: str,
        enable_thinking: bool = True,
    ) -> None:
        if not sampler_path:
            raise ValueError(
                "model.sampler_path (or PE_TINKER_SAMPLER_PATH) is required for backend=tinker"
            )
        if not api_key:
            raise ValueError(
                "TINKER_API_KEY (or model.tinker_api_key) is required for backend=tinker"
            )
        self.sampler_path = sampler_path
        self.api_key = api_key
        self.base_model = base_model
        self.renderer_name = renderer_name
        self.enable_thinking = enable_thinking
        self._sampling_client: Any = None
        self._renderer: Any = None
        self._tokenizer: Any = None
        self.chat = SimpleNamespace(completions=self)

    @classmethod
    def from_config(cls, cfg: HarnessConfig) -> TinkerSamplingChatClient:
        m = cfg.model
        return cls(
            sampler_path=m.sampler_path,
            api_key=m.resolved_tinker_api_key(),
            base_model=m.base_model,
            renderer_name=m.resolved_renderer(cfg.rollout.enable_thinking),
            enable_thinking=cfg.rollout.enable_thinking,
        )

    async def _ensure_ready(self) -> None:
        if self._sampling_client is not None:
            return
        tinker, renderers, get_tokenizer = _require_tinker()
        service = tinker.ServiceClient(api_key=self.api_key)
        self._sampling_client = await service.create_sampling_client_async(
            model_path=self.sampler_path
        )
        self._tokenizer = get_tokenizer(self.base_model)
        self._renderer = renderers.get_renderer(
            self.renderer_name, self._tokenizer, model_name=self.base_model
        )

    def _encode(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None, enable_thinking: bool) -> list[int]:
        kwargs: dict[str, Any] = {}
        template = getattr(self._tokenizer, "chat_template", None)
        if isinstance(template, str) and "enable_thinking" in template:
            kwargs["enable_thinking"] = enable_thinking
        encoding = self._tokenizer.apply_chat_template(
            _normalize_messages(messages),
            tools=tools or None,
            add_generation_prompt=True,
            **kwargs,
        )
        if hasattr(encoding, "input_ids"):
            return list(encoding.input_ids)
        return list(encoding)

    async def create(
        self,
        *,
        model: str = "",
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        temperature: float = 1.0,
        top_p: float = 0.8,
        presence_penalty: float = 0.0,
        max_tokens: int = 4096,
        extra_body: dict[str, Any] | None = None,
        **_ignored: Any,
    ) -> ChatCompletion:
        del tool_choice, presence_penalty
        await self._ensure_ready()
        tinker, _, _ = _require_tinker()
        extra_body = extra_body or {}
        top_k = extra_body.get("top_k", -1)
        ctk = extra_body.get("chat_template_kwargs") or {}
        enable_thinking = self.enable_thinking
        if "enable_thinking" in ctk:
            enable_thinking = bool(ctk["enable_thinking"])

        token_ids = self._encode(messages, tools, enable_thinking)
        result = await self._sampling_client.sample_async(
            prompt=tinker.types.ModelInput.from_ints(token_ids),
            num_samples=1,
            sampling_params=tinker.types.SamplingParams(
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                top_k=int(top_k) if top_k and int(top_k) > 0 else -1,
            ),
        )
        sequence = result.sequences[0]
        parsed, _ = self._renderer.parse_response(sequence.tokens)
        openai_message = self._renderer.to_openai_message(parsed)
        tool_call_objs = _tool_calls_from_openai_message(openai_message)

        payload: dict[str, Any] = {
            "content": openai_message.get("content") or None,
            "role": "assistant",
            "tool_calls": tool_call_objs,
        }
        reasoning = openai_message.get("reasoning_content")
        if reasoning:
            payload["reasoning_content"] = reasoning
        try:
            msg = ChatCompletionMessage.model_validate(payload)
        except Exception:
            msg = ChatCompletionMessage(
                content=payload["content"],
                role="assistant",
                tool_calls=tool_call_objs,
            )
            if reasoning:
                object.__setattr__(msg, "reasoning_content", reasoning)

        return ChatCompletion(
            id=str(uuid.uuid4()),
            choices=[
                Choice(
                    finish_reason=_finish_reason(str(sequence.stop_reason), bool(tool_call_objs)),  # type: ignore[arg-type]
                    index=0,
                    message=msg,
                )
            ],
            created=int(time.time()),
            model=model or self.sampler_path,
            object="chat.completion",
            usage=CompletionUsage(
                completion_tokens=len(sequence.tokens),
                prompt_tokens=len(token_ids),
                total_tokens=len(sequence.tokens) + len(token_ids),
            ),
        )
