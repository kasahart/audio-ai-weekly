import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import pytest
import yaml

import model_utils
from model_utils import (
    build_chat_kwargs,
    build_token_kwargs,
    create_client,
    get_ai_config,
    get_api_key,
    supports_custom_temperature,
)


SETTINGS = {
    "ai": {"provider": "github_models"},
    "github_models": {
        "api_key_env": "GITHUB_TOKEN",
        "endpoint": "https://models.example/v1",
        "model": "openai/gpt-5",
    },
    "gemini": {
        "api_key_env": "GEMINI_API_KEY",
        "endpoint": "https://gemini.example/openai/",
        "model": "gemini-3.5-flash",
    },
}


class TestProviderConfiguration:
    def test_repository_settings_define_both_providers(self):
        root = Path(__file__).parent.parent
        settings = yaml.safe_load((root / "config/settings.yaml").read_text())
        assert settings["ai"]["provider"] == "gemini"
        assert settings["github_models"]["api_key_env"] == "GITHUB_TOKEN"
        assert (
            settings["github_models"]["endpoint"]
            == "https://models.github.ai/inference"
        )
        assert settings["github_models"]["model"] == "openai/gpt-4.1"
        assert settings["gemini"] == {
            "api_key_env": "GEMINI_API_KEY",
            "endpoint": "https://generativelanguage.googleapis.com/v1beta/openai/",
            "model": "gemini-3.5-flash",
            "feature_max_tokens": 64000,
            "max_tokens": 16000,
            "batch_size": 5,
            "batch_max_tokens": 32000,
            "min_request_interval": 60.0,
            "retry_max": 3,
            "retry_interval": 5.0,
        }

    def test_selects_github_models(self):
        provider, config = get_ai_config(SETTINGS)
        assert provider == "github_models"
        assert config["model"] == "openai/gpt-5"
        assert config["endpoint"] == "https://models.example/v1"
        assert (
            get_api_key(provider, config, {"GITHUB_TOKEN": "github-key"})
            == "github-key"
        )

    def test_selects_gemini(self):
        settings = {**SETTINGS, "ai": {"provider": "gemini"}}
        provider, config = get_ai_config(settings)
        assert provider == "gemini"
        assert config["model"] == "gemini-3.5-flash"
        assert config["endpoint"] == "https://gemini.example/openai/"
        assert (
            get_api_key(provider, config, {"GEMINI_API_KEY": "gemini-key"})
            == "gemini-key"
        )

    def test_unknown_provider_is_an_explicit_error(self):
        settings = {**SETTINGS, "ai": {"provider": "unknown"}}
        with pytest.raises(ValueError, match="Unknown AI provider 'unknown'"):
            get_ai_config(settings)

    def test_missing_key_names_provider_and_environment_variable(self):
        provider, config = get_ai_config(SETTINGS)
        with pytest.raises(EnvironmentError, match="github_models.*GITHUB_TOKEN"):
            get_api_key(provider, config, {})

    def test_create_client_uses_selected_endpoint_and_key(self, monkeypatch):
        captured = {}

        def fake_openai(**kwargs):
            captured.update(kwargs)
            return object()

        monkeypatch.setattr(model_utils, "OpenAI", fake_openai)
        settings = {**SETTINGS, "ai": {"provider": "gemini"}}
        create_client(settings, {"GEMINI_API_KEY": "secret"})
        assert captured == {
            "base_url": "https://gemini.example/openai/",
            "api_key": "secret",
        }


class TestSupportsCustomTemperature:
    def test_gpt4o_supports_temperature(self):
        assert supports_custom_temperature("gpt-4o") is True

    def test_gpt4o_mini_supports_temperature(self):
        assert supports_custom_temperature("gpt-4o-mini") is True

    def test_gpt5_does_not_support_temperature(self):
        assert supports_custom_temperature("gpt-5") is False

    def test_gpt5_turbo_does_not_support_temperature(self):
        assert supports_custom_temperature("gpt-5-turbo") is False

    def test_provider_prefixed_gpt5_does_not_support_temperature(self):
        assert supports_custom_temperature("openai/gpt-5") is False

    def test_gemini3_does_not_support_custom_temperature(self):
        assert supports_custom_temperature("gemini-3.5-flash") is False

    def test_o1_supports_temperature(self):
        # o1 does not start with gpt-5, so supports temperature
        assert supports_custom_temperature("o1") is True


class TestBuildTokenKwargs:
    def test_gpt4o_uses_max_tokens(self):
        result = build_token_kwargs("gpt-4o", 1000)
        assert result == {"max_tokens": 1000}

    def test_gpt5_uses_max_completion_tokens(self):
        result = build_token_kwargs("gpt-5", 500)
        assert result == {"max_completion_tokens": 500}

    def test_provider_prefixed_gpt5_uses_max_completion_tokens(self):
        result = build_token_kwargs("openai/gpt-5", 500)
        assert result == {"max_completion_tokens": 500}

    def test_o1_uses_max_completion_tokens(self):
        result = build_token_kwargs("o1", 200)
        assert result == {"max_completion_tokens": 200}

    def test_o3_uses_max_completion_tokens(self):
        result = build_token_kwargs("o3", 100)
        assert result == {"max_completion_tokens": 100}

    def test_o4_uses_max_completion_tokens(self):
        result = build_token_kwargs("o4", 300)
        assert result == {"max_completion_tokens": 300}


class TestBuildChatKwargs:
    def test_gpt4o_with_temperature(self):
        result = build_chat_kwargs("gpt-4o", 1000, temperature=0.3)
        assert result == {"max_tokens": 1000, "temperature": 0.3}

    def test_gpt4o_without_temperature(self):
        result = build_chat_kwargs("gpt-4o", 1000)
        assert result == {"max_tokens": 1000}
        assert "temperature" not in result

    def test_gpt5_ignores_temperature(self):
        result = build_chat_kwargs("gpt-5", 500, temperature=0.5)
        assert result == {
            "max_completion_tokens": 500,
            "reasoning_effort": "minimal",
        }
        assert "temperature" not in result

    def test_provider_prefixed_gpt5_ignores_temperature(self):
        result = build_chat_kwargs("openai/gpt-5", 500, temperature=0.5)
        assert result == {
            "max_completion_tokens": 500,
            "reasoning_effort": "minimal",
        }

    def test_temperature_none_is_excluded(self):
        result = build_chat_kwargs("gpt-4o", 800, temperature=None)
        assert "temperature" not in result

    def test_gemini3_uses_default_temperature_and_medium_reasoning(self):
        result = build_chat_kwargs("gemini-3.5-flash", 1000, temperature=0.3)
        assert result == {"max_tokens": 1000, "reasoning_effort": "medium"}

    def test_gemini3_accepts_reasoning_override(self):
        result = build_chat_kwargs(
            "gemini-3.5-flash", 1000, temperature=0.3, reasoning_effort="low"
        )
        assert result == {"max_tokens": 1000, "reasoning_effort": "low"}

    def test_non_reasoning_model_ignores_reasoning_override(self):
        result = build_chat_kwargs(
            "gpt-4o", 1000, temperature=0.3, reasoning_effort="low"
        )
        assert result == {"max_tokens": 1000, "temperature": 0.3}
