#!/usr/bin/env python3
"""Test connectivity to the configured AI provider."""

from pathlib import Path
import yaml

from model_utils import build_chat_kwargs, create_client, get_ai_config

ROOT = Path(__file__).parent.parent
SETTINGS = yaml.safe_load((ROOT / "config/settings.yaml").read_text())
CONNECTION_PROMPT = (ROOT / "config/prompts/connection.txt").read_text().strip()


def main():
    provider, cfg = get_ai_config(SETTINGS)
    try:
        client = create_client(SETTINGS)
    except EnvironmentError as e:
        print(f"❌ {e}")
        return
    try:
        resp = client.chat.completions.create(
            model=cfg["model"],
            messages=[{"role": "user", "content": CONNECTION_PROMPT}],
            **build_chat_kwargs(
                cfg["model"], SETTINGS["analysis"]["connection_max_tokens"]
            ),
        )
        print(f"✅ Connected to {provider}: {resp.choices[0].message.content}")
    except Exception as e:
        print(f"❌ Connection failed: {e}")


if __name__ == "__main__":
    main()
