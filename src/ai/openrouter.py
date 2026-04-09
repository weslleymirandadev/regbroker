"""OpenRouter streaming client."""
from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any

import httpx

BASE = "https://openrouter.ai/api/v1"


class OpenRouterError(Exception):
    pass


class OpenRouterClient:
    def __init__(self, api_key: str, model: str = "anthropic/claude-3.5-haiku"):
        self.api_key = api_key
        self.model   = model
        self._http   = httpx.Client(
            base_url=BASE,
            headers={
                "Authorization":  f"Bearer {api_key}",
                "HTTP-Referer":   "https://github.com/regbroker",
                "X-Title":        "RegBroker",
                "Content-Type":   "application/json",
            },
            timeout=120.0,
        )

    def set_model(self, model: str) -> None:
        self.model = model

    def validate_key(self) -> tuple[bool, str]:
        try:
            r = self._http.get("/auth/key")
            if r.status_code == 200:
                return True, r.json().get("data", {}).get("label", "valid")
            return False, f"HTTP {r.status_code}"
        except Exception as e:
            return False, str(e)

    def list_models(self) -> list[dict]:
        r = self._http.get("/models")
        r.raise_for_status()
        return r.json().get("data", [])

    def stream(
        self,
        messages: list[dict],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> Generator[str, None, None]:
        """Yields text delta chunks."""
        payload = {
            "model":       self.model,
            "messages":    messages,
            "max_tokens":  max_tokens,
            "temperature": temperature,
            "stream":      True,
        }
        with self._http.stream("POST", "/chat/completions",
                               content=json.dumps(payload)) as r:
            if r.status_code != 200:
                body = r.read().decode()
                raise OpenRouterError(f"HTTP {r.status_code}: {body[:400]}")
            for line in r.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    delta = (json.loads(data_str)
                             .get("choices", [{}])[0]
                             .get("delta", {})
                             .get("content", ""))
                    if delta:
                        yield delta
                except (json.JSONDecodeError, IndexError, KeyError):
                    continue

    def complete(
        self,
        messages: list[dict],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        on_chunk: Any = None,
    ) -> str:
        full = ""
        for chunk in self.stream(messages, max_tokens=max_tokens,
                                  temperature=temperature):
            full += chunk
            if on_chunk:
                on_chunk(chunk)
        return full
