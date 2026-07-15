"""Configuration and request helpers for OpenAI-compatible AI providers."""

import os
from collections.abc import Mapping

from openai import OpenAI


def get_ai_config(settings: Mapping) -> tuple[str, Mapping]:
    """Return the selected provider name and its configuration."""
    ai = settings.get("ai")
    provider = ai.get("provider") if isinstance(ai, Mapping) else None
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
    settings: Mapping, environ: Mapping[str, str] | None = None
) -> OpenAI:
    """Create an OpenAI client for the provider selected in settings."""
    provider, config = get_ai_config(settings)
    return OpenAI(
        base_url=config["endpoint"],
        api_key=get_api_key(provider, config, environ),
    )


def supports_custom_temperature(model: str) -> bool:
    model_name = model.rsplit("/", 1)[-1]
    return not model_name.startswith(("gpt-5", "gemini-3"))


def build_token_kwargs(model: str, max_tokens: int) -> dict:
    model_name = model.rsplit("/", 1)[-1]
    if model_name.startswith(("gpt-5", "o1", "o3", "o4")):
        return {"max_completion_tokens": max_tokens}
    return {"max_tokens": max_tokens}


def build_chat_kwargs(
    model: str, max_tokens: int, temperature: float | None = None
) -> dict:
    kwargs = build_token_kwargs(model, max_tokens)
    model_name = model.rsplit("/", 1)[-1]
    if model_name.startswith("gpt-5"):
        kwargs["reasoning_effort"] = "minimal"
    elif model_name.startswith("gemini-3"):
        kwargs["reasoning_effort"] = "medium"
    if temperature is not None and supports_custom_temperature(model):
        kwargs["temperature"] = temperature
    return kwargs
