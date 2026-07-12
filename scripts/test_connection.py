#!/usr/bin/env python3
"""Test connectivity to GitHub Models."""

import os
from pathlib import Path
import yaml
from openai import OpenAI

from model_utils import build_chat_kwargs

ROOT = Path(__file__).parent.parent
SETTINGS = yaml.safe_load((ROOT / "config/settings.yaml").read_text())


def main():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("❌ GITHUB_TOKEN is not set")
        return
    cfg = SETTINGS["github_models"]
    client = OpenAI(base_url=cfg["endpoint"], api_key=token)
    try:
        resp = client.chat.completions.create(
            model=cfg["model"],
            messages=[{"role": "user", "content": "Hello. Reply with just 'OK'."}],
            **build_chat_kwargs(cfg["model"], 10),
        )
        print(f"✅ Connected to GitHub Models: {resp.choices[0].message.content}")
    except Exception as e:
        print(f"❌ Connection failed: {e}")


if __name__ == "__main__":
    main()
