#!/usr/bin/env python3
"""Minimal COS — Phase 1 with Grok API call."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests  # pip install requests

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "specs" / "META-COS.md"
LOG = Path(__file__).resolve().parent / "event_log.jsonl"

GROK_API_KEY = os.environ.get("GROK_API_KEY")  # set this in your environment
GROK_URL = "https://api.x.ai/v1/chat/completions"


def log_event(
    event_type: str, summary: str, actor: str = "cos", refs=None, detail=None
):
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": event_type,
        "actor": actor,
        "summary": summary,
        "refs": refs or [],
    }
    if detail:
        event["detail"] = detail
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
    return event


def ask_grok(prompt: str, system: str = None) -> str:
    if system is None:
        system = (
            "You are the local Chief of Staff (COS) described in META-COS.md. "
            "Follow its principles: spec-driven, hierarchical, inspectable, "
            "self-improving, and local-first. Grok is the default executor. "
            "Be concise and actionable."
        )
    # ... rest of the function stays the same

    if not GROK_API_KEY:
        raise RuntimeError("GROK_API_KEY environment variable not set")
    headers = {
        "Authorization": f"Bearer {GROK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "grok-4.7",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    r = requests.post(GROK_URL, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    reply = r.json()["choices"][0]["message"]["content"]
    log_event(
        "grok_call",
        "Grok API call completed",
        actor="grok",
        detail={"prompt_preview": prompt[:120], "reply_preview": reply[:200]},
    )
    return reply


def main():
    print("COS ready.")
    print(f"Spec exists: {SPEC.exists()}")
    log_event("startup", "COS started", refs=["specs/META-COS.md"])

    print("\nCOS interactive (type 'quit' to exit)")
    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if user_input.lower() in ("quit", "exit", "q"):
            break
        if not user_input:
            continue

        log_event("user_directive", user_input, actor="user")
        reply = ask_grok(user_input)
        print(f"Grok: {reply}")

        
if __name__ == "__main__":
    main()
