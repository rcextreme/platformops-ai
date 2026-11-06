"""LLM provider abstraction.

Business logic depends only on `LLMProvider.complete()`. Provider-specific
HTTP details live in the concrete classes below. Configuration is read from
the environment at call time; nothing is hardcoded.

Environment variables:
  LLM_PROVIDER      "openai_compatible" (chat-completions style API)
  LLM_API_BASE_URL  e.g. https://host/v1  (no default)
  LLM_MODEL         model name (no default)
  LLM_API_KEY       API key (no default)
  LLM_TIMEOUT_SECONDS  optional, default 60
"""

import os
from abc import ABC, abstractmethod

import httpx


class LLMUnavailableError(Exception):
    """The provider is not configured or could not be reached/used."""

    def __init__(self, reason: str, code: str = "provider_unavailable"):
        super().__init__(reason)
        self.reason = reason
        self.code = code


class LLMProvider(ABC):
    name: str = "unknown"
    model: str | None = None

    @abstractmethod
    def complete(self, system: str, user: str) -> str:
        """Return the model's text reply or raise LLMUnavailableError."""


class UnconfiguredProvider(LLMProvider):
    name = "unconfigured"

    def __init__(self, reason: str):
        self.reason = reason

    def complete(self, system: str, user: str) -> str:
        raise LLMUnavailableError(self.reason, code="provider_not_configured")


class OpenAICompatibleProvider(LLMProvider):
    name = "openai_compatible"

    def __init__(self, base_url: str, model: str, api_key: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self.timeout = timeout

    def complete(self, system: str, user: str) -> str:
        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self.model,
                    "temperature": 0.2,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as exc:
            raise LLMUnavailableError(
                f"LLM provider returned HTTP {exc.response.status_code}."
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(
                f"LLM provider request failed: {type(exc).__name__}."
            ) from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMUnavailableError(
                "LLM provider returned an unexpected response shape."
            ) from exc


def get_llm_provider() -> LLMProvider:
    """FastAPI dependency: build a provider from env, or an unconfigured stub."""
    kind = os.getenv("LLM_PROVIDER", "").strip().lower()
    base_url = os.getenv("LLM_API_BASE_URL", "").strip()
    model = os.getenv("LLM_MODEL", "").strip()
    api_key = os.getenv("LLM_API_KEY", "").strip()

    missing = [
        name
        for name, value in (
            ("LLM_PROVIDER", kind),
            ("LLM_API_BASE_URL", base_url),
            ("LLM_MODEL", model),
            ("LLM_API_KEY", api_key),
        )
        if not value
    ]
    if missing:
        return UnconfiguredProvider(
            "LLM provider is not configured. Missing: " + ", ".join(missing) + "."
        )
    if kind != "openai_compatible":
        return UnconfiguredProvider(f"Unsupported LLM_PROVIDER '{kind}'.")

    try:
        timeout = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))
    except ValueError:
        timeout = 60.0
    return OpenAICompatibleProvider(base_url, model, api_key, timeout)
