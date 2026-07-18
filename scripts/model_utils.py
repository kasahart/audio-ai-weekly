"""Configuration and request helpers for OpenAI-compatible AI providers."""

import os
import threading
from collections.abc import Mapping

from openai import OpenAI


_REQUEST_BUDGETS: dict[tuple[str, int], "RequestBudget"] = {}
_REQUEST_BUDGETS_LOCK = threading.Lock()


class RequestLimitExceeded(RuntimeError):
    """Raised before a provider request would exceed the configured run budget."""

    status_code = 429


class _BudgetedCompletions:
    def __init__(self, completions, budget: "RequestBudget"):
        self._completions = completions
        self._budget = budget

    def create(self, *args, **kwargs):
        self._budget.consume()
        return self._completions.create(*args, **kwargs)


class _BudgetedChat:
    def __init__(self, chat, budget: "RequestBudget"):
        self._chat = chat
        self.completions = _BudgetedCompletions(chat.completions, budget)

    def __getattr__(self, name):
        return getattr(self._chat, name)


class BudgetedOpenAI:
    def __init__(self, client, budget: "RequestBudget"):
        self._client = client
        self._budget = budget
        self.chat = _BudgetedChat(client.chat, budget)

    def with_options(self, *args, **kwargs):
        return BudgetedOpenAI(self._client.with_options(*args, **kwargs), self._budget)

    def __getattr__(self, name):
        return getattr(self._client, name)


class RequestBudget:
    def __init__(self, provider: str, limit: int):
        self.provider = provider
        self.limit = max(1, int(limit))
        self.used = 0
        self._lock = threading.Lock()

    def consume(self):
        with self._lock:
            if self.used >= self.limit:
                raise RequestLimitExceeded(
                    f"AI provider {self.provider} reached its per-run request "
                    f"limit ({self.limit}); refusing to send another request"
                )
            self.used += 1
            print(
                f"[ai-budget] {self.provider} request "
                f"{self.used}/{self.limit}"
            )


def get_request_budget(provider: str, limit: int) -> RequestBudget:
    """Return the process-wide request budget for a provider and limit."""
    key = (provider, max(1, int(limit)))
    with _REQUEST_BUDGETS_LOCK:
        if key not in _REQUEST_BUDGETS:
            _REQUEST_BUDGETS[key] = RequestBudget(*key)
        return _REQUEST_BUDGETS[key]


def get_ai_config(
    settings: Mapping, provider: str | None = None
) -> tuple[str, Mapping]:
    """Return the selected provider name and its configuration."""
    ai = settings.get("ai")
    provider = provider or (ai.get("provider") if isinstance(ai, Mapping) else None)
    if (
        not provider
        or provider not in settings
        or not isinstance(settings[provider], Mapping)
    ):
        raise ValueError(
            f"Unknown AI provider {provider!r}; set ai.provider to a configured provider"
        )
    return provider, settings[provider]


def get_api_key(
    provider: str, config: Mapping, environ: Mapping[str, str] | None = None
) -> str:
    """Read the selected provider's API key from its configured environment variable."""
    env_name = config.get("api_key_env")
    if not env_name:
        raise ValueError(f"AI provider {provider!r} has no api_key_env setting")
    environment = os.environ if environ is None else environ
    api_key = environment.get(env_name)
    if not api_key:
        raise EnvironmentError(
            f"AI provider {provider!r} requires environment variable {env_name}"
        )
    return api_key


def create_client(
    settings: Mapping,
    environ: Mapping[str, str] | None = None,
    provider: str | None = None,
) -> OpenAI:
    """Create an OpenAI client for the provider selected in settings."""
    provider, config = get_ai_config(settings, provider)
    client = OpenAI(
        base_url=config["endpoint"],
        api_key=get_api_key(provider, config, environ),
    )
    request_limit = config.get("request_limit_per_run")
    if request_limit is not None:
        client = BudgetedOpenAI(client, get_request_budget(provider, request_limit))
    return client


def supports_custom_temperature(model: str) -> bool:
    model_name = model.rsplit("/", 1)[-1]
    return not model_name.startswith(("gpt-5", "gemini-3"))


def build_token_kwargs(model: str, max_tokens: int) -> dict:
    model_name = model.rsplit("/", 1)[-1]
    if model_name.startswith(("gpt-5", "o1", "o3", "o4")):
        return {"max_completion_tokens": max_tokens}
    return {"max_tokens": max_tokens}


def build_chat_kwargs(
    model: str,
    max_tokens: int,
    temperature: float | None = None,
    reasoning_effort: str | None = None,
) -> dict:
    kwargs = build_token_kwargs(model, max_tokens)
    model_name = model.rsplit("/", 1)[-1]
    if model_name.startswith("gpt-5"):
        kwargs["reasoning_effort"] = reasoning_effort or "minimal"
    elif model_name.startswith("gemini-3"):
        kwargs["reasoning_effort"] = reasoning_effort or "medium"
    if temperature is not None and supports_custom_temperature(model):
        kwargs["temperature"] = temperature
    return kwargs
