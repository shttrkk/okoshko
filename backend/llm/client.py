"""OpenAI-compatible JSON transport with a bounded, validated offline fallback.

Only plans and text extraction belong here. This module never creates evidence.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections.abc import Callable
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from backend.contracts import Mode
from backend.security import contains_runtime_secret

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger(__name__)


class UnsafeOutput(ValueError):
    """Provider output must not be returned, cached or sent to JSON repair."""


class LLMClient:
    def __init__(self, repo=None, *, transport: httpx.BaseTransport | None = None):
        self.repo = repo
        self.base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self.api_key = os.getenv("LLM_API_KEY", "")
        self.model = os.getenv("LLM_MODEL", "")
        self.mode: Mode = "live" if os.getenv("LLM_MODE", "offline") == "live" and self.api_key and self.model else "offline"
        self.structured_mode = os.getenv("LLM_STRUCTURED_MODE", "auto")
        self.timeout = float(os.getenv("LLM_TIMEOUT_S", "12"))
        self.transport = transport

    def structured(
        self,
        output_model: type[T],
        messages: list[dict[str, str]],
        fallback: Callable[[], T | dict],
        *,
        task: str = "structured",
        budget_s: float = 2.4,
    ) -> tuple[T, Mode]:
        schema = output_model.model_json_schema()
        signature = {"model": self.model, "base_url": self.base_url, "messages": messages, "schema": schema, "task": task}
        key = hashlib.sha256(json.dumps(signature, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if self.repo:
            cached = self.repo.get("llm_cache", key)
            if cached:
                try:
                    value = output_model.model_validate(cached["value"])
                    self._check_output(value)
                    return value, "cache"
                except (KeyError, ValidationError, UnsafeOutput):
                    logger.warning("Discarding invalid LLM cache entry for task %s", task)
        if self.mode == "live":
            deadline = time.monotonic() + budget_s
            modes = {"auto": ["native", "json", "prompted"], "native": ["native", "json", "prompted"],
                     "json": ["json", "prompted"], "prompted": ["prompted"]}.get(self.structured_mode, ["native", "json", "prompted"])
            system = {"role": "system", "content": "Return only JSON matching the supplied schema. Do not invent evidence or factual hotel values. Schema: " + json.dumps(schema, ensure_ascii=False)}
            request_messages = [system, *messages]
            last_content = None
            with httpx.Client(transport=self.transport) as client:
                for mode in modes:
                    remaining = deadline - time.monotonic()
                    if remaining <= .05:
                        break
                    body = {"model": self.model, "messages": request_messages, "max_tokens": 2500}
                    if mode == "native":
                        body["response_format"] = {"type": "json_schema", "json_schema": {"name": output_model.__name__, "strict": True, "schema": schema}}
                    elif mode == "json":
                        body["response_format"] = {"type": "json_object"}
                    try:
                        response = client.post(self.base_url + "/chat/completions", json=body,
                                               headers={"Authorization": "Bearer " + self.api_key},
                                               timeout=min(self.timeout, remaining))
                        if response.status_code in {401, 403, 404, 429} or response.status_code >= 500:
                            logger.warning("LLM task %s unavailable (HTTP %s); deterministic fallback", task, response.status_code)
                            break
                        response.raise_for_status()
                        last_content = self._message_content(response.json())
                        value = self._parse(output_model, last_content)
                        self._save(key, value, task)
                        return value, "live"
                    except UnsafeOutput:
                        last_content = None
                        logger.info("LLM task %s output rejected by security boundary", task)
                        break
                    except (httpx.HTTPError, ValidationError, json.JSONDecodeError, KeyError, IndexError, ValueError, TypeError):
                        logger.info("LLM task %s mode %s not validated", task, mode)
                # One bounded repair, only if a model actually returned malformed JSON.
                remaining = deadline - time.monotonic()
                if last_content and remaining > .1:
                    try:
                        response = client.post(self.base_url + "/chat/completions",
                            json={"model": self.model, "messages": [system, {"role": "user", "content": "Repair this JSON without adding information: " + last_content}], "max_tokens": 2500},
                            headers={"Authorization": "Bearer " + self.api_key}, timeout=min(self.timeout, remaining))
                        response.raise_for_status()
                        value = self._parse(output_model, self._message_content(response.json()))
                        self._save(key, value, task)
                        return value, "live"
                    except (httpx.HTTPError, ValidationError, json.JSONDecodeError, KeyError, IndexError, ValueError, TypeError):
                        logger.info("LLM repair did not validate for %s", task)
        value = output_model.model_validate(fallback())
        self._check_output(value)
        return value, "offline"

    def _message_content(self, payload) -> str:
        message = payload["choices"][0]["message"]
        if not isinstance(message, dict) or message.get("refusal") or "tool_calls" in message or "function_call" in message:
            raise UnsafeOutput("Provider actions are not accepted")
        content = message.get("content")
        if not isinstance(content, str) or len(content) > 100_000:
            raise UnsafeOutput("Provider text is not bounded JSON")
        if contains_runtime_secret(content, (self.api_key,)):
            raise UnsafeOutput("Provider output contains a protected value")
        return content

    def _check_output(self, value: BaseModel):
        if contains_runtime_secret(value.model_dump(mode="json"), (self.api_key,)):
            raise UnsafeOutput("Validated output contains a protected value")

    def _parse(self, output_model: type[T], content: str) -> T:
        text = content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        value = output_model.model_validate_json(text)
        self._check_output(value)
        return value

    def _save(self, key: str, value: BaseModel, task: str):
        if self.repo:
            self.repo.upsert("llm_cache", {"id": key, "task": task, "value": value.model_dump(mode="json"), "model": self.model})

