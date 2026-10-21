#!/usr/bin/env python3
"""Minimal COS stub — Phase 1 starter."""

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "specs" / "META-COS.md"
LOG = Path(__file__).resolve().parent / "event_log.jsonl"


def log_event(event_type: str, summary: str, actor: str = "cos", refs=None):
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": event_type,
        "actor": actor,
        "summary": summary,
        "refs": refs or [],
    }
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
    return event


def main():
    print("COS stub ready.")
    print(f"Spec exists: {SPEC.exists()} ({SPEC})")
    log_event("startup", "COS stub started", refs=["specs/META-COS.md"])
    print("Startup event written to event_log.jsonl")


if __name__ == "__main__":
    main()
