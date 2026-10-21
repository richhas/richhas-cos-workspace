#!/usr/bin/env python3
"""Minimal local COS — Phase 1 with read/write."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests  # pip install requests

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "specs" / "META-COS.md"
LOG = Path(__file__).resolve().parent / "event_log.jsonl"

GROK_API_KEY = os.environ.get("GROK_API_KEY")
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


def resolve_safe(rel_path: str) -> Path:
    """Resolve a relative path and ensure it stays inside ROOT."""
    target = (ROOT / rel_path).resolve()
    if not str(target).startswith(str(ROOT.resolve())):
        raise ValueError("Path escapes workspace root")
    return target


def read_file(rel_path: str) -> str:
    target = resolve_safe(rel_path)
    if not target.is_file():
        raise FileNotFoundError(rel_path)
    content = target.read_text(encoding="utf-8")
    log_event(
        "file_read",
        f"Read {rel_path}",
        detail={"path": rel_path, "bytes": len(content.encode("utf-8"))},
    )
    return content


def write_file(rel_path: str, content: str) -> Path:
    target = resolve_safe(rel_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    log_event(
        "file_write",
        f"Wrote {rel_path}",
        detail={"path": rel_path, "bytes": len(content.encode("utf-8"))},
    )
    return target


def ask_grok(prompt: str, system: str = None) -> str:
    if system is None:
        system = (
            "You are the local Chief of Staff (COS) described in META-COS.md. "
            "Follow its principles: spec-driven, hierarchical, inspectable, "
            "self-improving, and local-first. Grok is the default executor. "
            "Be concise and actionable."
        )
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
    r = requests.post(GROK_URL, headers=headers, json=payload, timeout=90)
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
    print("Commands:")
    print("  handoff: <task>")
    print("  read: <relative/path>")
    print("  write: <relative/path>")
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

        # Claude hand-off
        if user_input.lower().startswith("handoff:"):
            task = user_input[8:].strip()
            handoff_prompt = (
                "You are Claude acting as the implementation specialist for the local COS.\n"
                "Follow the principles in META-COS.md (spec-driven, local-first, inspectable).\n"
                "Grok is the director; you perform the heavy implementation.\n\n"
                f"Task:\n{task}\n\n"
                "Produce the concrete code or file changes required. "
                "Be complete and ready to drop into the repo."
            )
            print("\n--- Claude hand-off prompt (copy below) ---\n")
            print(handoff_prompt)
            print("\n--- end hand-off ---\n")
            log_event(
                "claude_handoff",
                task,
                actor="cos",
                detail={"prompt_preview": handoff_prompt[:200]},
            )
            continue

        # Read file
        if user_input.lower().startswith("read:"):
            rel_path = user_input[5:].strip()
            try:
                content = read_file(rel_path)
                print(f"\n--- {rel_path} ---\n")
                print(content)
                print("--- end ---")
            except Exception as e:
                print(f"Read failed: {e}")
            continue

        # Write file
        if user_input.lower().startswith("write:"):
            rel_path = user_input[6:].strip()
            print(f"Enter content for {rel_path} (end with a line containing only . )")
            lines = []
            while True:
                line = input()
                if line.strip() == ".":
                    break
                lines.append(line)
            content = "\n".join(lines) + "\n"
            try:
                path = write_file(rel_path, content)
                print(f"Wrote {path}")
            except Exception as e:
                print(f"Write failed: {e}")
            continue

        # Normal directive → Grok
        log_event("user_directive", user_input, actor="user")
        reply = ask_grok(user_input)
        print(f"Grok: {reply}")


if __name__ == "__main__":
    main()
