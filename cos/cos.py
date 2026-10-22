#!/usr/bin/env python3
"""Minimal local COS — Phase 1 with deep persistent context.

Loads specs/META-COS.md once at startup into the system prompt.
Each Grok call is prefixed with up to 1_000_000 bytes of deep context
from cos/context.md plus a compact tail of event_log.jsonl.
Normal user messages run an agentic tool-calling loop (max 10 rounds).
Tools: list_dir, read_file, write_file, update_task, git, handoff. No shell.
Open work lives in cos/tasks.md and is loaded into every Grok call.
Console output is wrapped to the terminal width so long replies do not overflow.

Git is a bounded subprocess of the git binary only. No general shell.
Commit and push require an explicit user approval phrase in the current
directive. Paths must stay inside the workspace. No force, no config writes.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests  # pip install requests

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "specs" / "META-COS.md"
LOG = Path(__file__).resolve().parent / "event_log.jsonl"
CONTEXT = Path(__file__).resolve().parent / "context.md"
TASKS = Path(__file__).resolve().parent / "tasks.md"

GROK_API_KEY = os.environ.get("GROK_API_KEY")
GROK_URL = "https://api.x.ai/v1/chat/completions"

# Newest material from context.md + event-log tail, capped for each Grok call.
CONTEXT_BYTE_LIMIT = 1_000_000
EVENT_TAIL_LINES = 30
MAX_ENTRY_CHARS = 4000

# Agentic loop: one round is one Grok API call. Tool calls in that response
# are executed before the next call. Stop at a final answer or this cap.
MAX_TOOL_ROUNDS = 10
TOOL_RESULT_CHAR_LIMIT = 120_000

# Directory listing is bounded, workspace-scoped, and does not follow symlinks.
LIST_DIR_MAX_ENTRIES = 400
LIST_DIR_MAX_DEPTH = 6
_LIST_DIR_SKIP_EXPAND = frozenset({
    ".git",
    "__pycache__",
    ".venv",
    "node_modules",
    ".idea",
})

TASK_STATUSES = ("open", "doing", "blocked", "done")
TASK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
TASK_LINE_RE = re.compile(
    r"^- \[(?P<mark>[ xX])\] \*\*(?P<id>[a-z0-9][a-z0-9-]{0,39})\*\* "
    r"\((?P<status>open|doing|blocked|done)\) (?P<title>.+)$"
)

# Console wrap. File writes are never wrapped. Width is re-read per print so a
# resized terminal is honored. COLUMNS overrides a missing or tiny tty size.
CONSOLE_WIDTH_MIN = 40
CONSOLE_WIDTH_MAX = 120
CONSOLE_WIDTH_DEFAULT = 80

# Bounded git. Subprocess of the git binary only. No shell, no force, no config.
GIT_TIMEOUT_SEC = 120
GIT_OUTPUT_CHAR_LIMIT = 20_000
GIT_ACTIONS = ("status", "diff", "log", "add", "commit", "push")
GIT_APPROVAL_RE = re.compile(
    r"\b(?:approve|approved|approval)\s+git\s+(commit|push)\b",
    re.IGNORECASE,
)
# Current user directive, set for the duration of one tool loop. Git commit
# and push read it for the approval phrase. Never trusted from tool arguments.
_TURN_DIRECTIVE = ""

AVAILABLE_COMMANDS = (
    "- list: <relative/path>\n"
    "- read: <relative/path>\n"
    "- write: <relative/path>\n"
    "- improve: <relative/path> | <instruction>\n"
    "- handoff: <task>\n"
    "- tasks\n"
    "- git status | git diff | git log"
)

AGENT_SYSTEM_EXTRA = (
    "Tools for this turn: list_dir(path), read_file(path), write_file(path, content), "
    "update_task(id, status, title, note), git(action, paths, message, remote, branch), "
    "handoff(task). "
    "Use list_dir first when you need to see what files exist; do not guess paths. "
    "list_dir path is workspace-relative; pass '.' for the workspace root. "
    "It returns workspace-relative paths you can pass to read_file. "
    "It does not follow symlinks, does not expand .git, and is depth- and entry-capped. "
    "Use read_file and write_file to inspect and change files, and handoff to emit a Claude hand-off. "
    "Open work is in cos/tasks.md (also shown below when present). "
    "Use update_task to add or change a task; do not rewrite cos/tasks.md with write_file. "
    "Statuses: open, doing, blocked, done. At most one task may be doing. "
    "Mark a task doing before you start it and done only after the file change is written. "
    "Git is a bounded tool, not a shell. Allowed actions: status, diff, log, add, commit, push. "
    "status, diff, and log need no approval. add stages explicit workspace paths only. "
    "commit and push run only when the current user directive contains the exact phrase "
    "'approve git commit' or 'approve git push'. A prior session approval does not count. "
    "Do not invent that phrase. If it is absent, report the status and the phrase required; do not commit or push. "
    "Never pass extra git flags. No force, no reset, no clean, no config, no commit --amend. "
    "You may call tools across multiple rounds; results are returned to you in this turn. "
    "Do not claim you cannot read or list local files. "
    "Do not emit manual CLI lines (list:, read:, write:, improve:, handoff:, tasks); "
    "those are user commands, not your interface. "
    "Do not run shell or network actions other than the git tool; only the provided tools. "
    "write_file content must be the raw file body, with no markdown fences. "
    "The user sees only a one-line trace per tool call, not the tool result body, "
    "so your final answer must include the findings, decisions, and paths you changed. "
    "When finished, reply with that concise final answer and no tool calls."
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": (
                "List files and directories in the COS workspace. "
                "Use this before guessing paths. Path is workspace-relative; "
                "pass '.' to list the workspace root. Returns workspace-relative "
                "paths and file sizes. Does not follow symlinks. Does not expand "
                ".git, __pycache__, .venv, node_modules, or .idea. "
                "Depth and entry count are capped; pass a narrower path if truncated."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Workspace-relative directory. Use '.' for the workspace root.",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a UTF-8 text file from the COS workspace by relative path. "
                "Path must stay inside the workspace (example: cos/cos.py, specs/META-COS.md)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Workspace-relative file path.",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Create or replace a UTF-8 text file in the workspace. "
                "Content is the complete raw file body, not a diff and not markdown-fenced. "
                "Parent directories are created if needed. Path must stay inside the workspace. "
                "Do not use this for cos/tasks.md; use update_task."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Workspace-relative file path to write.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Complete new file content.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_task",
            "description": (
                "Add or update one task in cos/tasks.md. "
                "id is a short slug (lowercase letters, digits, hyphens). "
                "status is open, doing, blocked, or done. "
                "title is required when creating a task. "
                "note is an optional one-line reason, appended to the task. "
                "At most one task may be doing; setting doing moves any other doing task back to open. "
                "Does not commit or push."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Task slug, e.g. project-spec.",
                    },
                    "status": {
                        "type": "string",
                        "description": "open, doing, blocked, or done.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Short task title. Required for a new id.",
                    },
                    "note": {
                        "type": "string",
                        "description": "Optional one-line note.",
                    },
                },
                "required": ["id", "status"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git",
            "description": (
                "Bounded git against the workspace repo. Not a shell. "
                "Actions: status, diff, log, add, commit, push. "
                "status/diff/log need no approval. add takes explicit workspace paths. "
                "commit requires message plus the phrase 'approve git commit' in the current user directive. "
                "push requires the phrase 'approve git push' in the current user directive. "
                "remote defaults to origin, branch to main. No extra flags, no force, no config."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "status, diff, log, add, commit, or push.",
                    },
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Workspace-relative paths for add, or optional path limit for diff.",
                    },
                    "message": {
                        "type": "string",
                        "description": "Commit message. Required for commit. One line, no newlines.",
                    },
                    "remote": {
                        "type": "string",
                        "description": "Push remote. Default origin. Letters, digits, dot, underscore, hyphen.",
                    },
                    "branch": {
                        "type": "string",
                        "description": "Push branch. Default main. No refs/heads prefix, no force.",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "handoff",
            "description": (
                "Emit a self-contained Claude hand-off prompt for the user to paste into "
                "a VS Code Claude session. Does not call Claude. Use for heavy implementation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "Complete task description for Claude.",
                    }
                },
                "required": ["task"],
            },
        },
    },
]

CONTEXT_HEADER = (
    "# COS Deep Context\n\n"
    "Persistent accumulation of significant dialog, decisions, and "
    "file-change summaries across COS invocations.\n"
    "On each Grok call the newest material from this file plus a compact "
    "event-log tail is loaded, up to 1_000_000 bytes.\n"
    "Older material is dropped when the accumulated context exceeds that limit.\n"
)

TASKS_HEADER = (
    "# COS Tasks\n\n"
    "Durable open-work list. Survives restarts. Updated only via update_task.\n"
    "Statuses: open, doing, blocked, done. At most one task is doing.\n"
    "Do not edit this file by hand during a tool turn; use update_task.\n"
)

DEEP_CONTEXT_HEADER = "Deep context (persistent):"

# Full meta-spec text, loaded once at process startup (not per API call).
_META_COS_TEXT = None


def log_event(
    event_type: str,
    summary: str,
    actor: str = "cos",
    refs=None,
    detail=None,
    **extra,
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
    for key, value in extra.items():
        if key not in event:
            event[key] = value
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
    return event


def resolve_safe(rel_path: str) -> Path:
    root = ROOT.resolve()
    target = (root / rel_path).resolve()
    if target != root and root not in target.parents:
        raise ValueError("Path escapes workspace root")
    return target


def _clip(text: str, limit: int) -> str:
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    text = text.replace("\r\n", "\n")
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def _read_text_safe(path: Path) -> str:
    """Read text, returning '' on any failure. Never raises."""
    try:
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
    except OSError:
        return ""
    except Exception:
        return ""


def _fit_newest(text: str, max_bytes: int) -> str:
    """Keep the newest suffix of text that fits in max_bytes (UTF-8)."""
    if not text or max_bytes <= 0:
        return ""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    start = len(raw) - max_bytes
    while start < len(raw) and (raw[start] & 0xC0) == 0x80:
        start += 1
    if start >= len(raw):
        return ""
    return raw[start:].decode("utf-8")


def _trim_context_file(text: str, limit: int) -> str:
    """Keep newest material and a leading truncation note, within limit bytes."""
    note = (
        "\n\n[TRUNCATED: context.md exceeded 1_000_000 bytes; "
        "newest material kept, older material omitted]\n\n"
    )
    note_b = len(note.encode("utf-8"))
    if note_b >= limit:
        return _fit_newest(text, limit)
    combined = note + _fit_newest(text, limit - note_b)
    if len(combined.encode("utf-8")) > limit:
        return _fit_newest(combined, limit)
    return combined


def ensure_context_file() -> None:
    """Create cos/context.md if missing. Trim on disk if it already exceeds 1 MB."""
    try:
        CONTEXT.parent.mkdir(parents=True, exist_ok=True)
        if not CONTEXT.exists():
            CONTEXT.write_text(CONTEXT_HEADER, encoding="utf-8")
            return
        raw = _read_text_safe(CONTEXT)
        original = len(raw.encode("utf-8"))
        if original > CONTEXT_BYTE_LIMIT:
            trimmed = _trim_context_file(raw, CONTEXT_BYTE_LIMIT)
            CONTEXT.write_text(trimmed, encoding="utf-8")
            log_event(
                "context_truncation",
                (
                    f"context.md exceeded {CONTEXT_BYTE_LIMIT} bytes at startup; "
                    f"kept newest {len(trimmed.encode('utf-8'))} bytes"
                ),
                refs=["cos/context.md"],
                detail={
                    "context_bytes": len(trimmed.encode("utf-8")),
                    "original_bytes": original,
                    "limit": CONTEXT_BYTE_LIMIT,
                },
            )
    except OSError:
        return
    except Exception:
        return


def append_context(summary: str) -> None:
    """Append a significant note to cos/context.md. Fault-tolerant.

    If the file exceeds CONTEXT_BYTE_LIMIT, keep the newest material and log it.
    """
    text = _clip((summary or "").strip(), MAX_ENTRY_CHARS)
    if not text:
        return
    try:
        CONTEXT.parent.mkdir(parents=True, exist_ok=True)
        if not CONTEXT.exists():
            try:
                CONTEXT.write_text(CONTEXT_HEADER, encoding="utf-8")
            except OSError:
                pass
        ts = datetime.now(timezone.utc).isoformat()
        entry = f"\n## {ts}\n{text}\n"
        existing = _read_text_safe(CONTEXT)
        combined = existing + entry
        original = len(combined.encode("utf-8"))
        truncated = original > CONTEXT_BYTE_LIMIT
        if truncated:
            combined = _trim_context_file(combined, CONTEXT_BYTE_LIMIT)
        CONTEXT.write_text(combined, encoding="utf-8")
        if truncated:
            kept = len(combined.encode("utf-8"))
            log_event(
                "context_truncation",
                (
                    f"context.md exceeded {CONTEXT_BYTE_LIMIT} bytes; "
                    f"kept newest {kept} bytes"
                ),
                refs=["cos/context.md"],
                detail={
                    "context_bytes": kept,
                    "original_bytes": original,
                    "limit": CONTEXT_BYTE_LIMIT,
                },
            )
    except OSError:
        return
    except Exception:
        return


def _one_line(text: str, limit: int = 160) -> str:
    text = " ".join((text or "").replace("\r", " ").replace("\n", " ").split())
    return _clip(text, limit)


def console_width() -> int:
    """Usable terminal columns, clamped. Never raises. Re-read on each print.

    COLUMNS is an explicit override when set to a positive integer. Otherwise
    the live tty size is used. Result is always in [CONSOLE_WIDTH_MIN, MAX].
    """
    width = 0
    env = os.environ.get("COLUMNS", "").strip()
    if env.isdigit():
        width = int(env)
    if width <= 0:
        for stream in (sys.stdout, sys.stderr):
            try:
                width = shutil.get_terminal_size(fallback=(0, 24)).columns
            except (OSError, ValueError, AttributeError):
                width = 0
            if width > 0:
                break
            try:
                width = os.get_terminal_size(stream.fileno()).columns
            except (OSError, ValueError, AttributeError):
                width = 0
            if width > 0:
                break
    if width <= 0:
        width = CONSOLE_WIDTH_DEFAULT
    if width < CONSOLE_WIDTH_MIN:
        width = CONSOLE_WIDTH_MIN
    if width > CONSOLE_WIDTH_MAX:
        width = CONSOLE_WIDTH_MAX
    return width


def _char_width(ch: str) -> int:
    """Visible columns for one character. Tabs are 4. Controls are 0."""
    if ch == "\t":
        return 4
    o = ord(ch)
    if o < 32 or o == 127:
        return 0
    return 1


def _display_width(text: str) -> int:
    """Visible width. Tabs count as 4. Other controls count as 0."""
    return sum(_char_width(ch) for ch in text)


def _take_visible(text: str, budget: int):
    """Take a prefix whose visible width is <= budget. Returns (piece, rest)."""
    if budget <= 0 or not text:
        return "", text
    used = 0
    end = 0
    for i, ch in enumerate(text):
        w = _char_width(ch)
        if used + w > budget:
            break
        used += w
        end = i + 1
    if end == 0:
        # One character wider than the budget (should not happen at width>=4).
        # Drop it rather than emit an over-wide line.
        return "", text[1:]
    return text[:end], text[end:]


def _wrap_line(line: str, width: int) -> list:
    """Wrap one logical line so every output line has visible width <= width.

    Continuation lines keep a short leading indent when it still leaves room.
    Long tokens are hard-cut. Never emits a line wider than width.
    """
    if width < 1:
        width = 1
    if line == "":
        return [""]
    if _display_width(line) <= width:
        return [line]

    raw_indent = line[: len(line) - len(line.lstrip(" \t"))]
    indent = ""
    # Keep indent on continuations only when it leaves at least 8 columns.
    if raw_indent and _display_width(raw_indent) <= width - 8:
        indent = raw_indent

    out = []
    rest = line
    first = True
    guard = 0
    max_steps = max(8, _display_width(line) + 2)
    while rest and guard < max_steps:
        guard += 1
        prefix = "" if first else indent
        budget = width - _display_width(prefix)
        if budget < 1:
            prefix = ""
            budget = width
        if _display_width(rest) <= budget:
            out.append(prefix + rest)
            rest = ""
            break
        window, _after = _take_visible(rest, budget)
        if not window:
            # Cannot place even one character; stop rather than loop.
            break
        space = window.rfind(" ")
        # Break on a space only when the left piece is still useful.
        if space >= 8:
            piece = window[:space].rstrip()
            rest = rest[space + 1 :]
        else:
            piece = window.rstrip()
            rest = rest[len(window) :]
        if not piece and rest:
            # All-whitespace window; skip one character and continue.
            rest = rest[1:]
            first = False
            continue
        out.append(prefix + piece)
        first = False
    if rest:
        # Guard tripped. Hard-cut whatever remains so nothing overflows.
        while rest:
            prefix = "" if not out else indent
            budget = width - _display_width(prefix)
            if budget < 1:
                prefix = ""
                budget = width
            piece, rest = _take_visible(rest, budget)
            if not piece:
                rest = rest[1:]
                continue
            out.append(prefix + piece.rstrip())
    return out or [""]


def wrap_text(text: str, width: int = None) -> str:
    """Soft-wrap text to the console width. Does not alter stored file content.

    Every emitted line has visible width <= width. Tabs count as 4 columns.
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if width is None:
        width = console_width()
    try:
        width = int(width)
    except (TypeError, ValueError):
        width = CONSOLE_WIDTH_DEFAULT
    if width < 8:
        width = 8
    if width > CONSOLE_WIDTH_MAX:
        width = CONSOLE_WIDTH_MAX
    lines = []
    for line in text.split("\n"):
        lines.extend(_wrap_line(line, width))
    return "\n".join(lines)


def emit(text: str = "", end: str = "\n", file=None) -> None:
    """Print text wrapped to the current terminal width. Never raises."""
    stream = file if file is not None else sys.stdout
    try:
        rendered = wrap_text("" if text is None else str(text))
        print(rendered, end=end, file=stream, flush=True)
    except Exception:
        try:
            print(text, end=end, file=stream, flush=True)
        except Exception:
            return


def overflow_self_test(widths=None) -> str:
    """Check wrap_text never emits a line wider than the requested width.

    Returns a short report. Does not print the long fixtures (those would be
    the overflow under test). Safe to call from the REPL.
    """
    if widths is None:
        widths = (40, 80, 120)
    samples = {
        "spaces": "word " * 80,
        "token": "A" * 500,
        "indent": "    " + ("token " * 40),
        "tabs": "\t\t" + ("field " * 30),
        "mixed": "ok\n" + ("x" * 200) + "\n\tindented " + ("y" * 90),
        "short": "hello",
        "empty": "",
    }
    fails = []
    checked = 0
    for width in widths:
        for name, sample in samples.items():
            wrapped = wrap_text(sample, width)
            checked += 1
            for i, line in enumerate(wrapped.split("\n"), 1):
                vis = _display_width(line)
                if vis > width:
                    fails.append(f"{name}@{width} line {i} vis={vis}")
                    break
    if fails:
        return "FAIL overflow self-test: " + "; ".join(fails[:8])
    return f"PASS overflow self-test: {checked} cases, widths={list(widths)}, max line <= width"


def _validate_task_id(task_id: str) -> str:
    task_id = (task_id or "").strip().lower()
    if not TASK_ID_RE.match(task_id):
        raise ValueError(
            "id must be 1-40 chars: lowercase letters, digits, hyphens; "
            "must start with a letter or digit"
        )
    return task_id


def _validate_status(status: str) -> str:
    status = (status or "").strip().lower()
    if status not in TASK_STATUSES:
        raise ValueError("status must be open, doing, blocked, or done")
    return status


def _parse_task_line(line: str):
    match = TASK_LINE_RE.match(line.rstrip())
    if not match:
        return None
    status = match.group("status")
    mark = "x" if status == "done" else " "
    return {
        "id": match.group("id"),
        "status": status,
        "title": match.group("title").strip(),
        "mark": mark,
        "notes": [],
    }


def _format_task_block(task: dict) -> str:
    mark = "x" if task["status"] == "done" else " "
    lines = [
        f"- [{mark}] **{task['id']}** ({task['status']}) {task['title']}"
    ]
    for note in task.get("notes") or []:
        lines.append(f"  - {note}")
    return "\n".join(lines)


def _parse_tasks(text: str):
    """Return (preamble_lines, tasks, trailing_lines). Unknown lines stay in place."""
    tasks = []
    preamble = []
    trailing = []
    seen_task = False
    current = None
    for line in text.splitlines():
        parsed = _parse_task_line(line)
        if parsed:
            if current:
                tasks.append(current)
            current = parsed
            seen_task = True
            continue
        if current and line.startswith("  - "):
            current["notes"].append(line[4:].strip())
            continue
        if current:
            tasks.append(current)
            current = None
        if not seen_task:
            preamble.append(line)
        else:
            trailing.append(line)
    if current:
        tasks.append(current)
    return preamble, tasks, trailing


def _render_tasks(preamble, tasks, trailing) -> str:
    parts = []
    pre = "\n".join(preamble).rstrip()
    if pre:
        parts.append(pre)
    if tasks:
        parts.append("\n\n".join(_format_task_block(t) for t in tasks))
    trail = "\n".join(trailing).strip()
    if trail:
        parts.append(trail)
    text = "\n\n".join(parts).rstrip() + "\n"
    return text


def ensure_tasks_file() -> None:
    """Create cos/tasks.md if missing. Never raises."""
    try:
        TASKS.parent.mkdir(parents=True, exist_ok=True)
        if not TASKS.exists():
            TASKS.write_text(TASKS_HEADER, encoding="utf-8")
            log_event(
                "task_update",
                "Created cos/tasks.md",
                refs=["cos/tasks.md"],
                detail={"created": True},
            )
    except OSError:
        return
    except Exception:
        return


def load_tasks_text() -> str:
    ensure_tasks_file()
    return _read_text_safe(TASKS)


def task_summary() -> str:
    """Compact open-work block for the system prompt. Empty if no tasks."""
    text = load_tasks_text()
    _, tasks, _ = _parse_tasks(text)
    if not tasks:
        return ""
    active = [t for t in tasks if t["status"] != "done"]
    done = [t for t in tasks if t["status"] == "done"]
    lines = ["Open tasks (cos/tasks.md):"]
    if not active:
        lines.append("- (none open)")
    for task in active:
        note = ""
        if task["notes"]:
            note = " — " + _one_line(task["notes"][-1], 80)
        lines.append(
            f"- {task['id']} ({task['status']}): {_one_line(task['title'], 120)}{note}"
        )
    if done:
        lines.append(f"- done: {len(done)}")
    return "\n".join(lines)


def update_task(task_id: str, status: str, title: str = None, note: str = None) -> str:
    """Add or update one task. Enforces a single doing task. Returns the new file text."""
    task_id = _validate_task_id(task_id)
    status = _validate_status(status)
    title_in = _one_line(title, 160) if title else ""
    note_in = _one_line(note, 200) if note else ""

    ensure_tasks_file()
    raw = _read_text_safe(TASKS)
    if not raw.strip():
        raw = TASKS_HEADER
    preamble, tasks, trailing = _parse_tasks(raw)
    if not any(line.strip() for line in preamble):
        preamble = TASKS_HEADER.rstrip("\n").splitlines()

    found = None
    for task in tasks:
        if task["id"] == task_id:
            found = task
            break
    created = found is None
    if created:
        if not title_in:
            raise ValueError("title is required when creating a task")
        found = {
            "id": task_id,
            "status": status,
            "title": title_in,
            "notes": [],
        }
        tasks.append(found)
    else:
        if title_in:
            found["title"] = title_in
        found["status"] = status

    if note_in:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        found["notes"].append(f"{stamp}: {note_in}")
        found["notes"] = found["notes"][-8:]

    demoted = []
    if status == "doing":
        for task in tasks:
            if task["id"] != task_id and task["status"] == "doing":
                task["status"] = "open"
                demoted.append(task["id"])

    rendered = _render_tasks(preamble, tasks, trailing)
    TASKS.write_text(rendered, encoding="utf-8")
    summary = f"{'Created' if created else 'Updated'} task {task_id} -> {status}"
    if demoted:
        summary += "; demoted " + ", ".join(demoted)
    log_event(
        "task_update",
        summary,
        refs=["cos/tasks.md"],
        detail={
            "id": task_id,
            "status": status,
            "title": found["title"],
            "created": created,
            "demoted": demoted,
            "note": note_in,
        },
    )
    append_context(
        f"- Task: `{task_id}` {status} — {_clip(found['title'], 160)}"
        + (f"\n- Note: {_clip(note_in, 200)}" if note_in else "")
    )
    return rendered


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
    nbytes = len(content.encode("utf-8"))
    log_event(
        "file_write",
        f"Wrote {rel_path}",
        detail={"path": rel_path, "bytes": nbytes},
    )
    append_context(f"- File change: wrote `{rel_path}` ({nbytes} bytes)")
    return target


def list_dir(rel_path: str = ".") -> str:
    """List a workspace directory. Bounded, no symlink follow, no path escape.

    Returns workspace-relative paths and file sizes. Does not expand .git or
    other heavy directories. Raises on missing paths and escapes.
    """
    if not isinstance(rel_path, str):
        rel_path = str(rel_path or ".")
    raw = rel_path.strip().replace("\\", "/")
    raw = raw.rstrip("/") or "."
    target = resolve_safe(raw)
    if not target.exists():
        raise FileNotFoundError(raw)

    root = ROOT.resolve()

    def display_rel(path: Path) -> str:
        try:
            rel = path.relative_to(root)
        except ValueError:
            return path.name
        text = rel.as_posix()
        return text if text and text != "." else "."

    def ensure_inside(path: Path) -> Path:
        try:
            resolved = path.resolve()
        except OSError as e:
            raise ValueError(f"cannot resolve: {e}") from e
        if resolved != root and root not in resolved.parents:
            raise ValueError("Path escapes workspace root")
        return resolved

    resolved_target = ensure_inside(target)

    if resolved_target.is_file():
        try:
            size = resolved_target.stat().st_size
        except OSError:
            size = -1
        text = f"{display_rel(resolved_target)}  {size} B"
        log_event(
            "dir_list",
            f"Listed {raw}",
            detail={
                "path": raw,
                "entries": 1,
                "truncated": False,
                "bytes": len(text.encode("utf-8")),
            },
        )
        return text

    if not resolved_target.is_dir():
        raise ValueError(f"Not a directory: {raw}")

    lines = []
    count = 0
    truncated = False
    scan_cap = LIST_DIR_MAX_ENTRIES * 4

    def add(line: str) -> bool:
        nonlocal count, truncated
        if count >= LIST_DIR_MAX_ENTRIES:
            truncated = True
            return False
        lines.append(line)
        count += 1
        return True

    def sort_key(path: Path):
        try:
            is_dir = path.is_dir()
        except OSError:
            is_dir = False
        return (not is_dir, path.name.lower())

    header = display_rel(resolved_target)
    lines.append("." if header == "." else header + "/")

    def walk(directory: Path, depth: int) -> None:
        nonlocal truncated
        if truncated:
            return
        children = []
        try:
            for child in directory.iterdir():
                children.append(child)
                if len(children) >= scan_cap:
                    truncated = True
                    break
        except OSError as e:
            add(f"{display_rel(directory)}/  [error: {e}]")
            return
        children.sort(key=sort_key)
        for child in children:
            if truncated or count >= LIST_DIR_MAX_ENTRIES:
                truncated = True
                return
            try:
                if child.is_symlink():
                    if not add(f"{display_rel(child)}  symlink"):
                        return
                    continue
                ensure_inside(child)
                is_dir = child.is_dir()
                is_file = child.is_file()
            except (OSError, ValueError) as e:
                if not add(f"{child.name}  [skipped: {e}]"):
                    return
                continue
            shown = display_rel(child)
            if is_dir:
                if child.name in _LIST_DIR_SKIP_EXPAND:
                    if not add(f"{shown}/  [not expanded]"):
                        return
                    continue
                if depth + 1 >= LIST_DIR_MAX_DEPTH:
                    if not add(f"{shown}/  [not expanded: depth limit]"):
                        return
                    continue
                if not add(f"{shown}/"):
                    return
                walk(child, depth + 1)
            elif is_file:
                try:
                    size = child.stat().st_size
                except OSError:
                    size = -1
                if not add(f"{shown}  {size} B"):
                    return
            else:
                if not add(f"{shown}  other"):
                    return

    walk(resolved_target, 0)
    footer = (
        f"({count} entries, depth<={LIST_DIR_MAX_DEPTH}, symlinks not followed"
    )
    if truncated:
        footer += "; TRUNCATED at entry cap — pass a narrower path"
    footer += ")"
    lines.append(footer)
    text = "\n".join(lines)
    log_event(
        "dir_list",
        f"Listed {raw}",
        detail={
            "path": raw,
            "entries": count,
            "truncated": truncated,
            "bytes": len(text.encode("utf-8")),
        },
    )
    return text


def _git_binary() -> str:
    """Locate the git executable. Never returns a shell."""
    found = shutil.which("git")
    if not found:
        raise RuntimeError("git executable not found on PATH")
    return found


def _git_approval(kind: str) -> bool:
    """True only if the current user directive contains the approval phrase.

    Tool arguments cannot supply this. A previous turn's approval does not count.
    """
    if kind not in ("commit", "push"):
        return False
    text = _TURN_DIRECTIVE or ""
    for match in GIT_APPROVAL_RE.finditer(text):
        if match.group(1).lower() == kind:
            return True
    return False


def _git_rel_path(rel_path: str) -> str:
    """Validate a workspace-relative git path. Returns a forward-slash relative path.

    Rejects absolute paths, drive letters, '..', and '.git'. Does not require
    the path to exist (git add of a deletion, and git diff of a missing file,
    are valid). Still rejects escapes via resolve_safe when the path exists
    as a parent that can be resolved.
    """
    if not isinstance(rel_path, str):
        raise ValueError("path must be a string")
    raw = rel_path.strip().replace("\\", "/")
    if not raw or raw == ".":
        raise ValueError("path is required and cannot be '.' or empty")
    if raw.startswith("/") or raw.startswith("~"):
        raise ValueError(f"absolute path refused: {raw}")
    if len(raw) >= 2 and raw[1] == ":":
        raise ValueError(f"drive path refused: {raw}")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise ValueError(f"path escapes workspace: {raw}")
    if any(p == ".git" or p.lower() == ".git" for p in parts):
        raise ValueError("refusing to operate on .git")
    # Confirm the lexical path stays inside ROOT even if a parent is a symlink.
    root = ROOT.resolve()
    candidate = root.joinpath(*parts)
    try:
        resolved = candidate.resolve()
    except OSError as e:
        raise ValueError(f"cannot resolve {raw}: {e}") from e
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"path escapes workspace: {raw}")
    return "/".join(parts)


def _git_name(value: str, label: str, default: str) -> str:
    """Remote or branch name. No refs, no slashes, no flags."""
    text = (value or "").strip() or default
    if text.startswith("-") or "/" in text or "\\" in text or ".." in text:
        raise ValueError(f"invalid {label}: {text}")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", text):
        raise ValueError(f"invalid {label}: {text}")
    return text


def _normalize_git_paths(paths) -> list:
    """Return a list of validated workspace-relative paths. Empty list if none."""
    if paths is None:
        return []
    if isinstance(paths, str):
        raw_list = [paths]
    elif isinstance(paths, list):
        raw_list = paths
    else:
        raise ValueError("paths must be a list of strings")
    if len(raw_list) > 40:
        raise ValueError("too many paths (max 40)")
    cleaned = []
    for rel in raw_list:
        if not isinstance(rel, str):
            raise ValueError("each path must be a string")
        cleaned.append(_git_rel_path(rel))
    return cleaned


def _format_git_output(result: subprocess.CompletedProcess, argv: list) -> str:
    out = result.stdout or ""
    err = result.stderr or ""
    if not isinstance(out, str):
        out = str(out)
    if not isinstance(err, str):
        err = str(err)
    shown = "git " + " ".join(argv[1:])
    text = f"$ {shown}\nexit {result.returncode}\n"
    if out.strip():
        text += out
        if not out.endswith("\n"):
            text += "\n"
    if err.strip():
        text += "stderr:\n" + err
        if not err.endswith("\n"):
            text += "\n"
    if len(text) > GIT_OUTPUT_CHAR_LIMIT:
        text = text[:GIT_OUTPUT_CHAR_LIMIT] + "\n[TRUNCATED git output]\n"
    return text


def _build_git_argv(git_bin: str, root: str, action: str, path_list: list, message: str, remote: str, branch: str) -> list:
    """Build one argv for a known action. No shell. No extra flags."""
    argv = [git_bin, "-C", root]
    if action == "status":
        if path_list:
            raise ValueError("status does not take paths")
        argv += ["status", "--short", "--branch"]
    elif action == "diff":
        argv += ["diff", "--stat"]
        if path_list:
            argv.append("--")
            argv.extend(path_list)
    elif action == "log":
        if path_list:
            raise ValueError("log does not take paths")
        argv += ["log", "-5", "--oneline", "--decorate"]
    elif action == "add":
        if not path_list:
            raise ValueError("add requires explicit paths; refusing git add -A")
        argv += ["add", "--"]
        argv.extend(path_list)
    elif action == "commit":
        if not _git_approval("commit"):
            raise ValueError(
                "commit refused: current directive must contain 'approve git commit'"
            )
        msg = _one_line(message or "", 200)
        if len(msg) < 8:
            raise ValueError("commit message must be at least 8 characters, one line")
        if path_list:
            raise ValueError("commit does not take paths; use add first, then commit")
        argv += ["commit", "-m", msg]
    elif action == "push":
        if not _git_approval("push"):
            raise ValueError(
                "push refused: current directive must contain 'approve git push'"
            )
        if path_list:
            raise ValueError("push does not take paths")
        remote_name = _git_name(remote, "remote", "origin")
        branch_name = _git_name(branch, "branch", "main")
        # No --force, no --force-with-lease, no refspec rewrite.
        argv += ["push", remote_name, branch_name]
    else:
        raise ValueError(f"unhandled action: {action}")
    return argv


def run_git(action: str, paths=None, message: str = None, remote: str = None, branch: str = None) -> str:
    """Run one bounded git action in the workspace. Raises on refusal or failure.

    Does not use a shell. Does not accept extra flags. Commit and push require
    an approval phrase in the current user directive, not in the tool arguments.
    """
    action = (action or "").strip().lower()
    if action not in GIT_ACTIONS:
        raise ValueError(
            "action must be one of: " + ", ".join(GIT_ACTIONS)
        )
    path_list = _normalize_git_paths(paths)
    git_bin = _git_binary()
    root = str(ROOT.resolve())
    argv = _build_git_argv(git_bin, root, action, path_list, message, remote, branch)

    # Last-line defense: never pass a flag we did not construct.
    allowed_flags = {
        "status": {"--short", "--branch"},
        "diff": {"--stat", "--"},
        "log": {"-5", "--oneline", "--decorate"},
        "add": {"--"},
        "commit": {"-m"},
        "push": set(),
    }
    for arg in argv[3:]:
        if isinstance(arg, str) and arg.startswith("-") and arg not in allowed_flags[action]:
            raise ValueError(f"refusing unexpected git flag: {arg}")

    log_event(
        "git_call",
        f"git {action}",
        refs=[".git"],
        detail={
            "action": action,
            "argv_tail": argv[3:],
            "approved": action in ("commit", "push"),
        },
    )
    try:
        result = subprocess.run(
            argv,
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_SEC,
            shell=False,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"git {action} timed out after {GIT_TIMEOUT_SEC}s") from e
    except OSError as e:
        raise RuntimeError(f"git {action} failed to start: {e}") from e

    text = _format_git_output(result, argv)
    log_event(
        "git_result",
        f"git {action} exit {result.returncode}",
        detail={
            "action": action,
            "exit": result.returncode,
            "output_preview": _clip(text, 400),
        },
    )
    if action in ("commit", "push", "add"):
        append_context(
            f"- Git: {action} exit {result.returncode}\n"
            f"- Output: {_clip(text, 400)}"
        )
    if result.returncode != 0:
        raise RuntimeError(text.strip() or f"git {action} exit {result.returncode}")
    return text


def load_meta_cos() -> str:
    """Return specs/META-COS.md, reading the file at most once per process."""
    global _META_COS_TEXT
    if _META_COS_TEXT is None:
        try:
            _META_COS_TEXT = SPEC.read_text(encoding="utf-8")
        except OSError as e:
            _META_COS_TEXT = f"(unable to load specs/META-COS.md: {e})"
        except UnicodeDecodeError as e:
            _META_COS_TEXT = f"(unable to decode specs/META-COS.md: {e})"
    return _META_COS_TEXT


def build_system_prompt(extra: str = None) -> str:
    spec_text = load_meta_cos().rstrip()
    system = (
        "You are the local Chief of Staff (COS) described in META-COS.md. "
        "Follow its principles: spec-driven, hierarchical, inspectable, "
        "self-improving, and local-first. Grok is the default executor. "
        "Be concise and actionable. When asked to improve a file, return ONLY "
        "the complete new file content, no markdown fences, no commentary.\n\n"
        "=== specs/META-COS.md ===\n"
        f"{spec_text}\n"
        "=== end specs/META-COS.md ===\n\n"
        "Available commands:\n"
        f"{AVAILABLE_COMMANDS}\n\n"
        "You are in charge. Maintain and use the deep persistent context to drive multi-session work.\n"
        "Use the tools to drive progress and log every significant step."
    )
    tasks_block = task_summary()
    if tasks_block:
        system = system + "\n\n" + tasks_block
    if extra:
        system = system + "\n\n" + extra
    return system


def compact_event_tail(limit: int = EVENT_TAIL_LINES) -> str:
    """Compact newest event-log lines. Fault-tolerant; never raises."""
    try:
        if not LOG.is_file():
            return ""
        raw_lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    except Exception:
        return ""
    tail = [ln for ln in raw_lines if ln.strip()][-max(0, limit):]
    if not tail:
        return ""
    lines = []
    for ln in tail:
        try:
            ev = json.loads(ln)
        except json.JSONDecodeError:
            lines.append(_clip(ln, 200))
            continue
        if not isinstance(ev, dict):
            lines.append(_clip(ln, 200))
            continue
        ts = ev.get("ts", "")
        event_type = ev.get("type", "")
        actor = ev.get("actor", "")
        summary = ev.get("summary", "")
        if not isinstance(summary, str):
            summary = str(summary)
        summary = _clip(summary, 160)
        ctx_b = ev.get("context_bytes")
        detail = ev.get("detail")
        if ctx_b is None and isinstance(detail, dict):
            ctx_b = detail.get("context_bytes")
        suffix = f" context_bytes={ctx_b}" if ctx_b is not None else ""
        lines.append(f"- {ts} {event_type} ({actor}): {summary}{suffix}")
    return "\n".join(lines)


def load_deep_context(max_bytes: int = CONTEXT_BYTE_LIMIT):
    """Load newest deep context, capped at max_bytes.

    Returns (block, truncated, original_bytes).
    block is the exact text to prepend, always starting with the deep-context marker.
    Fault-tolerant: any failure yields a short marked block and does not raise.
    """
    try:
        return _load_deep_context_inner(max_bytes)
    except Exception as e:
        try:
            log_event(
                "context_load_error",
                f"Deep context load failed: {e}",
                detail={"error": _clip(str(e), 300)},
            )
        except Exception:
            pass
        fallback = (
            f"{DEEP_CONTEXT_HEADER}\n"
            "(context unavailable; load failed; continuing without history)\n"
        )
        fallback = _fit_newest(fallback, max_bytes) if max_bytes else ""
        return fallback, False, len(fallback.encode("utf-8"))


def _load_deep_context_inner(max_bytes: int):
    ctx_text = _read_text_safe(CONTEXT)
    tail = compact_event_tail(EVENT_TAIL_LINES)
    tasks_text = load_tasks_text().rstrip()

    if ctx_text.strip():
        ctx_section = (
            "=== cos/context.md ===\n"
            + ctx_text.rstrip()
            + "\n=== end cos/context.md ==="
        )
    else:
        ctx_section = "=== cos/context.md ===\n(empty)\n=== end cos/context.md ==="

    if tasks_text:
        tasks_section = (
            "=== cos/tasks.md ===\n"
            + tasks_text
            + "\n=== end cos/tasks.md ==="
        )
    else:
        tasks_section = "=== cos/tasks.md ===\n(empty)\n=== end cos/tasks.md ==="

    if tail:
        tail_section = (
            "=== event_log.jsonl (compact tail) ===\n"
            + tail
            + "\n=== end event_log.jsonl ==="
        )
    else:
        tail_section = (
            "=== event_log.jsonl (compact tail) ===\n"
            "(empty)\n"
            "=== end event_log.jsonl ==="
        )

    # Chronological: accumulated notes, then tasks, then newest event-log tail.
    # Suffix truncation therefore keeps the newest material.
    body = ctx_section + "\n\n" + tasks_section + "\n\n" + tail_section
    full = f"{DEEP_CONTEXT_HEADER}\n{body}\n"
    original_bytes = len(full.encode("utf-8"))
    if original_bytes <= max_bytes:
        return full, False, original_bytes

    banner = (
        f"{DEEP_CONTEXT_HEADER}\n"
        f"[TRUNCATED: accumulated context was {original_bytes} bytes; "
        f"limit is {max_bytes} bytes; newest material kept, older material omitted]\n"
    )
    banner_b = len(banner.encode("utf-8"))
    if banner_b >= max_bytes:
        kept = _fit_newest(banner, max_bytes)
        return kept, True, original_bytes
    newest = _fit_newest(body, max_bytes - banner_b)
    block = banner + newest
    if len(block.encode("utf-8")) > max_bytes:
        block = _fit_newest(block, max_bytes)
    return block, True, original_bytes


def ask_grok(prompt: str, system: str = None, context_summary: str = None) -> str:
    system = build_system_prompt(system)
    block, truncated, original_bytes = load_deep_context(CONTEXT_BYTE_LIMIT)
    raw = block.encode("utf-8")
    if len(raw) > CONTEXT_BYTE_LIMIT:
        block = _fit_newest(block, CONTEXT_BYTE_LIMIT)
        truncated = True
    context_bytes = len(block.encode("utf-8"))

    if truncated:
        log_event(
            "context_truncation",
            (
                f"Accumulated deep context {original_bytes} bytes exceeded "
                f"{CONTEXT_BYTE_LIMIT}; kept newest {context_bytes} bytes"
            ),
            refs=["cos/context.md", "cos/event_log.jsonl"],
            detail={
                "context_bytes": context_bytes,
                "original_bytes": original_bytes,
                "limit": CONTEXT_BYTE_LIMIT,
            },
        )

    # Prepend the measured block unchanged so context_bytes matches what is sent.
    user_message = f"{block}\n{prompt}"

    def _log_call(summary, detail):
        detail = dict(detail)
        detail["context_bytes"] = context_bytes
        detail["context_truncated"] = truncated
        detail["context_original_bytes"] = original_bytes
        log_event(
            "grok_call",
            summary,
            actor="grok",
            refs=["cos/context.md", "cos/event_log.jsonl"],
            detail=detail,
            context_bytes=context_bytes,
        )

    if not GROK_API_KEY:
        _log_call(
            "Grok API call failed: GROK_API_KEY not set",
            {"error": "GROK_API_KEY environment variable not set", "prompt_preview": prompt[:120]},
        )
        append_context(
            "- Dialog failed:\n"
            f"  - In: {_clip(prompt, 400)}\n"
            "  - Error: GROK_API_KEY environment variable not set"
        )
        raise RuntimeError("GROK_API_KEY environment variable not set")

    headers = {
        "Authorization": f"Bearer {GROK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "grok-4.7",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.2,
    }

    try:
        r = requests.post(GROK_URL, headers=headers, json=payload, timeout=600)
        r.raise_for_status()
        reply = r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        _log_call(
            f"Grok API call failed: {e}",
            {"prompt_preview": prompt[:120], "error": _clip(str(e), 600)},
        )
        append_context(
            "- Dialog failed:\n"
            f"  - In: {_clip(prompt, 400)}\n"
            f"  - Error: {_clip(str(e), 600)}"
        )
        raise

    summary = "Grok API call completed"
    if truncated:
        summary = (
            f"Grok API call completed; deep context truncated to {context_bytes} bytes"
        )
    _log_call(
        summary,
        {
            "prompt_preview": prompt[:120],
            "reply_preview": reply[:200],
        },
    )

    if context_summary:
        append_context(
            context_summary.rstrip()
            + f"\n- Grok reply preview: {_clip(reply, 500)}"
        )
    else:
        append_context(
            "- Dialog:\n"
            f"  - In: {_clip(prompt, 700)}\n"
            f"  - Out: {_clip(reply, 900)}"
        )
    return reply


def build_handoff_prompt(task: str) -> str:
    return (
        "You are Claude acting as the implementation specialist for the local COS.\n"
        "Follow the principles in META-COS.md (spec-driven, local-first, inspectable).\n"
        "Grok is the director; you perform the heavy implementation.\n\n"
        f"Task:\n{task}\n\n"
        "Produce the concrete code or file changes required. "
        "Be complete and ready to drop into the repo."
    )


def emit_handoff(task: str) -> str:
    """Print and log a Claude hand-off prompt. Returns the prompt text."""
    handoff_prompt = build_handoff_prompt(task)
    emit("\n--- Claude hand-off prompt (copy below) ---\n")
    emit(handoff_prompt)
    emit("\n--- end hand-off ---\n")
    log_event(
        "claude_handoff",
        task,
        actor="cos",
        detail={"prompt_preview": handoff_prompt[:200]},
    )
    append_context(
        "- Decision: Claude hand-off\n"
        f"- Task: {_clip(task, 500)}"
    )
    return handoff_prompt


def _tool_result_text(text: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    if len(text) <= TOOL_RESULT_CHAR_LIMIT:
        return text
    omitted = len(text) - TOOL_RESULT_CHAR_LIMIT
    return (
        text[:TOOL_RESULT_CHAR_LIMIT]
        + f"\n\n[TRUNCATED: tool result omitted {omitted} characters]\n"
    )


def _message_from_response(data):
    if not isinstance(data, dict):
        raise RuntimeError("Grok response was not a JSON object")
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("Grok response missing choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise RuntimeError("Grok response missing message")
    message = first.get("message")
    if not isinstance(message, dict):
        raise RuntimeError("Grok response missing message")
    return message


def _message_text(message) -> str:
    content = message.get("content") if isinstance(message, dict) else None
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "".join(parts)
    return str(content)


def _normalize_tool_calls(tool_calls):
    if not isinstance(tool_calls, list):
        return []
    cleaned = []
    for i, tc in enumerate(tool_calls):
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function")
        if not isinstance(fn, dict):
            fn = {}
        args = fn.get("arguments")
        if isinstance(args, str):
            args_text = args
        elif isinstance(args, dict):
            try:
                args_text = json.dumps(args)
            except TypeError:
                args_text = "{}"
        else:
            args_text = "{}"
        tc_id = tc.get("id")
        if not isinstance(tc_id, str) or not tc_id:
            tc_id = f"call_{i}"
        name = fn.get("name")
        if not isinstance(name, str):
            name = ""
        cleaned.append({
            "id": tc_id,
            "type": "function",
            "function": {
                "name": name,
                "arguments": args_text,
            },
        })
    return cleaned


def _parse_tool_args(raw):
    if isinstance(raw, dict):
        return raw, None
    if raw is None or raw == "":
        return {}, None
    if not isinstance(raw, str):
        return {}, "arguments must be a JSON object string"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        return {}, f"invalid JSON arguments: {e}"
    if not isinstance(parsed, dict):
        return {}, "arguments JSON must be an object"
    return parsed, None


def execute_tool(name: str, arguments: dict, round_i: int) -> str:
    """Run one tool call. Prints one trace line. Never raises."""
    if not isinstance(arguments, dict):
        arguments = {}
    label = name or "(unnamed)"
    try:
        if name == "list_dir":
            rel = str(arguments.get("path") or ".").strip() or "."
            text = list_dir(rel)
            nlines = text.count("\n") + (1 if text else 0)
            emit(f"[tool] list_dir {rel} -> {nlines} lines")
            log_event(
                "tool_exec",
                f"list_dir {rel}",
                detail={"tool": name, "path": rel, "lines": nlines, "round": round_i},
            )
            return text
        if name == "read_file":
            rel = str(arguments.get("path") or "").strip()
            if not rel:
                raise ValueError("path is required")
            content = read_file(rel)
            nbytes = len(content.encode("utf-8"))
            emit(f"[tool] read_file {rel} -> {nbytes} bytes")
            log_event(
                "tool_exec",
                f"read_file {rel}",
                detail={"tool": name, "path": rel, "bytes": nbytes, "round": round_i},
            )
            return content
        if name == "write_file":
            rel = str(arguments.get("path") or "").strip()
            content = arguments.get("content")
            if not rel:
                raise ValueError("path is required")
            if not isinstance(content, str):
                raise ValueError("content must be a string")
            norm = rel.replace("\\", "/").rstrip("/")
            if norm in ("cos/tasks.md", "tasks.md"):
                raise ValueError("use update_task for cos/tasks.md, not write_file")
            write_file(rel, content)
            nbytes = len(content.encode("utf-8"))
            emit(f"[tool] write_file {rel} -> {nbytes} bytes")
            log_event(
                "tool_exec",
                f"write_file {rel}",
                detail={"tool": name, "path": rel, "bytes": nbytes, "round": round_i},
            )
            return f"Wrote {rel} ({nbytes} bytes)"
        if name == "update_task":
            task_id = str(arguments.get("id") or "").strip()
            status = str(arguments.get("status") or "").strip()
            title = arguments.get("title")
            note = arguments.get("note")
            if not isinstance(title, str):
                title = None
            if not isinstance(note, str):
                note = None
            rendered = update_task(task_id, status, title, note)
            emit(f"[tool] update_task {task_id} -> {status}")
            log_event(
                "tool_exec",
                f"update_task {task_id} -> {status}",
                detail={
                    "tool": name,
                    "id": task_id,
                    "status": status,
                    "round": round_i,
                },
            )
            return rendered
        if name == "git":
            action = str(arguments.get("action") or "").strip().lower()
            paths = arguments.get("paths")
            message = arguments.get("message")
            remote = arguments.get("remote")
            branch = arguments.get("branch")
            if not isinstance(message, str):
                message = None
            if not isinstance(remote, str):
                remote = None
            if not isinstance(branch, str):
                branch = None
            text = run_git(action, paths, message, remote, branch)
            nlines = text.count("\n") + (1 if text else 0)
            emit(f"[tool] git {action} -> {nlines} lines")
            log_event(
                "tool_exec",
                f"git {action}",
                detail={"tool": name, "action": action, "lines": nlines, "round": round_i},
            )
            return text
        if name == "handoff":
            task = arguments.get("task")
            if not isinstance(task, str) or not task.strip():
                raise ValueError("task is required")
            task = task.strip()
            emit("[tool] handoff")
            emit_handoff(task)
            log_event(
                "tool_exec",
                "handoff",
                detail={"tool": name, "round": round_i, "task_preview": _clip(task, 200)},
            )
            return (
                "Claude hand-off prompt emitted to the user for task: "
                + _clip(task, 300)
            )
        raise ValueError(f"unknown tool: {name}")
    except Exception as e:
        err = _clip(str(e), 300)
        emit(f"[tool] {label} -> error: {err}")
        log_event(
            "tool_exec",
            f"{label} failed: {err}",
            detail={"tool": name, "error": err, "round": round_i},
        )
        return f"Error: {e}"


def ask_grok_with_tools(user_input: str) -> str:
    """Call Grok with tools. Execute tool_calls until a final answer or 10 rounds."""
    global _TURN_DIRECTIVE
    _TURN_DIRECTIVE = user_input or ""
    system_text = build_system_prompt(AGENT_SYSTEM_EXTRA)
    block, truncated, original_bytes = load_deep_context(CONTEXT_BYTE_LIMIT)
    raw = block.encode("utf-8")
    if len(raw) > CONTEXT_BYTE_LIMIT:
        block = _fit_newest(block, CONTEXT_BYTE_LIMIT)
        truncated = True
    context_bytes = len(block.encode("utf-8"))

    if truncated:
        log_event(
            "context_truncation",
            (
                f"Accumulated deep context {original_bytes} bytes exceeded "
                f"{CONTEXT_BYTE_LIMIT}; kept newest {context_bytes} bytes"
            ),
            refs=["cos/context.md", "cos/event_log.jsonl"],
            detail={
                "context_bytes": context_bytes,
                "original_bytes": original_bytes,
                "limit": CONTEXT_BYTE_LIMIT,
            },
        )

    def _log_call(summary, detail):
        detail = dict(detail)
        detail["context_bytes"] = context_bytes
        detail["context_truncated"] = truncated
        detail["context_original_bytes"] = original_bytes
        log_event(
            "grok_call",
            summary,
            actor="grok",
            refs=["cos/context.md", "cos/event_log.jsonl"],
            detail=detail,
            context_bytes=context_bytes,
        )

    if not GROK_API_KEY:
        _log_call(
            "Grok API call failed: GROK_API_KEY not set",
            {
                "error": "GROK_API_KEY environment variable not set",
                "prompt_preview": user_input[:120],
            },
        )
        append_context(
            "- Dialog failed:\n"
            f"  - In: {_clip(user_input, 400)}\n"
            "  - Error: GROK_API_KEY environment variable not set"
        )
        raise RuntimeError("GROK_API_KEY environment variable not set")

    headers = {
        "Authorization": f"Bearer {GROK_API_KEY}",
        "Content-Type": "application/json",
    }
    messages = [
        {"role": "system", "content": system_text},
        {"role": "user", "content": f"{block}\n{user_input}"},
    ]

    tools_used = []
    final_text = ""
    stopped_at_limit = False
    rounds_used = 0
    content = ""

    try:
        for round_i in range(1, MAX_TOOL_ROUNDS + 1):
            rounds_used = round_i
            payload = {
                "model": "grok-4.7",
                "messages": messages,
                "tools": TOOLS,
                "tool_choice": "auto",
                "temperature": 0.2,
            }
            preview = user_input if round_i == 1 else f"tool-results round {round_i}"
            try:
                r = requests.post(GROK_URL, headers=headers, json=payload, timeout=600)
                r.raise_for_status()
                message = _message_from_response(r.json())
            except Exception as e:
                err_text = str(e)
                resp = getattr(e, "response", None)
                if resp is not None:
                    try:
                        body = resp.text
                    except Exception:
                        body = ""
                    if body:
                        err_text = f"{e} | {_clip(body, 500)}"
                _log_call(
                    f"Grok API call failed: {err_text}",
                    {
                        "prompt_preview": preview[:120],
                        "error": _clip(err_text, 600),
                        "round": round_i,
                    },
                )
                raise RuntimeError(err_text) from e

            content = _message_text(message)
            tool_calls = _normalize_tool_calls(message.get("tool_calls"))
            tool_names = [tc["function"]["name"] for tc in tool_calls]

            summary = "Grok API call completed"
            if truncated:
                summary = (
                    f"Grok API call completed; deep context truncated to {context_bytes} bytes"
                )
            if tool_names:
                summary += "; tools: " + ", ".join(tool_names)
            _log_call(
                summary,
                {
                    "prompt_preview": preview[:120],
                    "reply_preview": (content or "")[:200],
                    "round": round_i,
                    "tool_calls": tool_names,
                },
            )

            assistant_msg = {"role": "assistant", "content": content or ""}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            messages.append(assistant_msg)

            if not tool_calls:
                final_text = content.strip() if content and content.strip() else "(empty response)"
                break

            for tc in tool_calls:
                name = tc["function"]["name"]
                args, arg_err = _parse_tool_args(tc["function"]["arguments"])
                if arg_err:
                    result = f"Error: {arg_err}"
                    shown = name or "(unnamed)"
                    emit(f"[tool] {shown} -> error: {arg_err}")
                    log_event(
                        "tool_exec",
                        f"{shown} failed",
                        detail={
                            "tool": name,
                            "error": _clip(arg_err, 300),
                            "round": round_i,
                        },
                    )
                else:
                    result = execute_tool(name, args, round_i)
                tools_used.append(name or "(unnamed)")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": _tool_result_text(result),
                })
        else:
            stopped_at_limit = True
            emit(f"[tool] max rounds ({MAX_TOOL_ROUNDS}) reached")
            log_event(
                "tool_limit",
                f"Stopped after {MAX_TOOL_ROUNDS} tool rounds",
                detail={"rounds": MAX_TOOL_ROUNDS, "tools": tools_used},
            )
            note = (
                f"Stopped after {MAX_TOOL_ROUNDS} tool rounds without a final answer."
            )
            if content and content.strip():
                final_text = _clip(content.strip(), 500) + "\n\n" + note
            else:
                final_text = note
    except Exception as e:
        append_context(
            "- Dialog failed:\n"
            f"  - In: {_clip(user_input, 400)}\n"
            f"  - Tools so far: {', '.join(tools_used) or '(none)'}\n"
            f"  - Error: {_clip(str(e), 600)}"
        )
        raise
    finally:
        _TURN_DIRECTIVE = ""

    log_event(
        "tool_loop",
        f"Tool loop finished in {rounds_used} round(s)",
        detail={
            "rounds": rounds_used,
            "tools": tools_used,
            "max_rounds": MAX_TOOL_ROUNDS,
            "stopped_at_limit": stopped_at_limit,
        },
    )
    append_context(
        "- Dialog:\n"
        f"  - In: {_clip(user_input, 700)}\n"
        f"  - Tools: {', '.join(tools_used) if tools_used else '(none)'} "
        f"({rounds_used} round(s))\n"
        f"  - Out: {_clip(final_text, 900)}"
    )
    return final_text


def print_commands():
    emit("Commands:")
    emit("  help")
    emit("  list: <relative/path>   (. or omit path for workspace root)")
    emit("  tasks")
    emit("  git status | git diff | git log")
    emit("  handoff: <task>")
    emit("  read: <relative/path>")
    emit("  write: <relative/path>")
    emit("  improve: <relative/path> | <instruction>")
    emit("  quit / exit / q")
    emit("  <anything else>  → sent to Grok")
    emit("Commit/push need 'approve git commit' or 'approve git push' in the directive.")


def _repl_git(user_input: str) -> None:
    """Manual read-only git commands. Commit and push stay on the tool path."""
    rest = user_input.strip()
    lowered = rest.lower()
    if lowered in ("git", "git:"):
        emit("Usage: git status | git diff | git log")
        emit("Commit and push are tool actions and need an approval phrase.")
        return
    if lowered.startswith("git:"):
        rest = rest[4:].strip()
    elif lowered.startswith("git "):
        rest = rest[4:].strip()
    else:
        emit("Usage: git status | git diff | git log")
        return
    action = rest.split()[0].lower() if rest else ""
    if action not in ("status", "diff", "log"):
        emit("REPL git is read-only: status, diff, log.")
        emit("To commit or push, send a directive that contains the approval phrase.")
        return
    try:
        emit(run_git(action))
    except Exception as e:
        emit(f"Git failed: {e}")


def main():
    spec_text = load_meta_cos()
    ensure_context_file()
    ensure_tasks_file()
    emit("COS ready.")
    emit(f"Spec exists: {SPEC.exists()}")
    ctx_bytes = _file_size(CONTEXT)
    emit(f"Deep context file: cos/context.md ({ctx_bytes} bytes)")
    task_bytes = _file_size(TASKS)
    emit(f"Task list: cos/tasks.md ({task_bytes} bytes)")
    log_event(
        "startup",
        "COS started; specs/META-COS.md loaded into system prompt",
        refs=["specs/META-COS.md", "cos/context.md", "cos/tasks.md"],
        detail={
            "bytes": len(spec_text.encode("utf-8")),
            "context_file_bytes": ctx_bytes,
            "tasks_file_bytes": task_bytes,
        },
    )
    append_context(
        "- Session start: COS started. Full META-COS.md loaded into the system prompt. "
        "Commands listed. Deep persistent context is active. Open tasks load from cos/tasks.md."
    )

    emit("\nCOS interactive (type 'quit' to exit)")
    print_commands()
    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            emit()
            break
        if user_input.lower() in ("quit", "exit", "q"):
            break
        if not user_input:
            continue

        if user_input.lower() in ("help", "help:", "?"):
            print_commands()
            log_event("help", "Printed available commands")
            continue

        if user_input.lower() in ("tasks", "tasks:", "task"):
            try:
                text = load_tasks_text()
                emit(text if text.strip() else "(no tasks)")
                log_event("task_list", "Printed cos/tasks.md", refs=["cos/tasks.md"])
            except Exception as e:
                emit(f"Tasks failed: {e}")
            continue

        # List directory (workspace-scoped; not a shell)
        lowered = user_input.lower()
        if lowered in ("list", "ls") or lowered.startswith("list:") or lowered.startswith("ls:"):
            if lowered.startswith("list:"):
                rel_path = user_input[5:].strip() or "."
            elif lowered.startswith("ls:"):
                rel_path = user_input[3:].strip() or "."
            else:
                rel_path = "."
            try:
                emit(list_dir(rel_path))
            except Exception as e:
                emit(f"List failed: {e}")
            continue

        if lowered in ("git", "git:") or lowered.startswith("git ") or lowered.startswith("git:"):
            _repl_git(user_input)
            continue

        # Claude hand-off
        if user_input.lower().startswith("handoff:"):
            task = user_input[8:].strip()
            emit_handoff(task)
            continue

        # Read file
        if user_input.lower().startswith("read:"):
            rel_path = user_input[5:].strip()
            try:
                content = read_file(rel_path)
                emit(f"\n--- {rel_path} ---\n")
                emit(content)
                emit("--- end ---")
            except Exception as e:
                emit(f"Read failed: {e}")
            continue

        # Write file
        if user_input.lower().startswith("write:"):
            rel_path = user_input[6:].strip()
            emit(f"Enter content for {rel_path} (end with a line containing only . )")
            lines = []
            while True:
                line = input()
                if line.strip() == ".":
                    break
                lines.append(line)
            content = "\n".join(lines) + "\n"
            try:
                path = write_file(rel_path, content)
                emit(f"Wrote {path}")
            except Exception as e:
                emit(f"Write failed: {e}")
            continue

        # Improve file via Grok
        if user_input.lower().startswith("improve:"):
            rest = user_input[8:].strip()
            if "|" not in rest:
                emit("Usage: improve: relative/path | instruction")
                continue
            rel_path, instruction = [p.strip() for p in rest.split("|", 1)]
            try:
                current = read_file(rel_path)
                append_context(
                    f"- Decision: improve `{rel_path}`\n"
                    f"- Instruction: {_clip(instruction, 500)}"
                )
                prompt = (
                    f"Improve the following file according to this instruction:\n{instruction}\n\n"
                    f"Current content of {rel_path}:\n\n{current}\n\n"
                    "Return ONLY the complete new file content. No markdown fences, no explanation."
                )
                emit(f"Asking Grok to improve {rel_path}...")
                new_content = ask_grok(
                    prompt,
                    context_summary=(
                        f"- Decision: Grok improve of `{rel_path}`\n"
                        f"- Instruction: {_clip(instruction, 500)}\n"
                        "- Note: full file body omitted from deep context; result written to disk"
                    ),
                )
                # Strip accidental fences if Grok added them
                if new_content.startswith("```"):
                    new_content = "\n".join(new_content.split("\n")[1:-1])
                write_file(rel_path, new_content)
                log_event(
                    "self_improve",
                    f"Improved {rel_path}",
                    detail={"instruction": instruction},
                )
                emit(f"Improved and wrote {rel_path}")
            except Exception as e:
                emit(f"Improve failed: {e}")
            continue

        # Normal directive → Grok with tools
        log_event("user_directive", user_input, actor="user")
        try:
            reply = ask_grok_with_tools(user_input)
            emit(f"Grok: {reply}")
        except Exception as e:
            emit(f"Grok call failed: {e}")


if __name__ == "__main__":
    main()
