#!/usr/bin/env python3
"""Minimal local COS — Phase 1 with deep persistent context.

Loads specs/META-COS.md once at startup into the system prompt.
Loads cos/prompts/ask-first.md on each prompt build. That policy overrides
backlog pressure: questions and statements are not permission to act.
Each Grok call is prefixed with up to 1_000_000 bytes of deep context
from cos/context.md plus a compact tail of event_log.jsonl.
Normal user messages run an agentic tool-calling loop (max 40 rounds).
Tools: list_dir, read_file, write_file, update_task, git, handoff,
file_info, file_outline, read_file_range, read_file_head, read_file_tail,
search_in_file, search_files, list_files, patch_file, run_check.
run_check runs python on one workspace script. It is not a shell.
patch_file is one unique literal match and a short diff. It does not replace write_file.
Open work lives in cos/tasks.md and is loaded into every Grok call.
Console output is wrapped to the terminal width so long replies do not overflow.

Git is a bounded subprocess of the git binary only. No general shell.
run_check is a bounded subprocess of the python binary only. One workspace
script, no flags, no shell, 60 second timeout. It does not run cos/cos.py.
Commit and push require an explicit user approval phrase in the current
directive. Paths must stay inside the workspace. No force, no config writes.

A successful push ends the tool loop. Logging rewrites context and the event
log, so chasing a clean tree after push never converges inside one turn.
If the round cap is hit, one extra no-tools call produces the final answer.

Tool-result character cap stays off until partial IO is in place and the
user verifies that. TOOL_RESULT_CHAR_LIMIT is None. Full tool text is
returned to the model. Do not restore the cap in this step.
Partial reads are additive. They do not replace read_file or write_file.
"""

import hashlib
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
POLICY = Path(__file__).resolve().parent / "prompts" / "ask-first.md"

GROK_API_KEY = os.environ.get("GROK_API_KEY")
GROK_URL = "https://api.x.ai/v1/chat/completions"

# Newest material from context.md + event-log tail, capped for each Grok call.
CONTEXT_BYTE_LIMIT = 1_000_000
EVENT_TAIL_LINES = 30
MAX_ENTRY_CHARS = 4000

# Agentic loop: one round is one Grok API call. Tool calls in that response
# are executed before the next call. Stop at a final answer or this cap.
# The cap does not include the one no-tools summary call used when it is hit.
MAX_TOOL_ROUNDS = 40
# Tool-result character cap stays off (user 2026-10-23).
# None means _tool_result_text returns the full string.
# Do not restore until partial IO is in place and the user verifies that.
# Step 3 adds partial reads beside full read_file. It does not turn the cap on.
TOOL_RESULT_CHAR_LIMIT = None

# file_outline bounds. These limit outline work, not the tool-result cap.
OUTLINE_MAX_BYTES = 2_000_000
OUTLINE_MAX_LINES = 8_000
OUTLINE_MAX_ITEMS = 200
EDGE_LINES = 8
EDGE_CHARS = 120

# Partial reads. Bounds are on the request, not a replacement for read_file.
# An over-wide range errors. It does not fall back to the whole file.
PARTIAL_READ_MAX_LINES = 400
PARTIAL_READ_DEFAULT_N = 40
SEARCH_IN_FILE_MAX_MATCHES = 50
SEARCH_IN_FILE_MAX_CONTEXT = 5
SEARCH_IN_FILE_MAX_PATTERN = 200

# Cross-file search and glob listing. Additive. Do not replace list_dir
# or search_in_file. Recursion is off unless asked. .git is not expanded.
SEARCH_FILES_MAX_MATCHES = 50
SEARCH_FILES_MAX_FILES = 200
SEARCH_FILES_MAX_FILE_BYTES = 1_000_000
LIST_FILES_MAX_ENTRIES = 400

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

_MD_HEADING = re.compile(r"^(#{1,6})[ \t]+(\S.*)$")
_PY_DEF = re.compile(
    r"^([ \t]*)(?:async[ \t]+)?def[ \t]+([A-Za-z_][A-Za-z0-9_]*)[ \t]*\("
)
_PY_CLASS = re.compile(r"^([ \t]*)class[ \t]+([A-Za-z_][A-Za-z0-9_]*)\b")
_KT_FUN = re.compile(
    r"^([ \t]*)(?:(?:public|private|internal|protected|open|override|"
    r"suspend|inline|tailrec|operator|infix|external)[ \t]+)*"
    r"fun[ \t]+([A-Za-z_][A-Za-z0-9_]*)[ \t]*\("
)
_KT_CLASS = re.compile(
    r"^([ \t]*)(?:(?:public|private|internal|protected|open|abstract|"
    r"sealed|data|enum|inner|value)[ \t]+)*"
    r"(class|interface|object)[ \t]+([A-Za-z_][A-Za-z0-9_]*)\b"
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

# Bounded local check. Python binary only. Not a shell. Not run_command.
# One workspace script, args that are not flags, 60 second timeout.
# Refuses anything under cos/. Does not commit. Does not restore the result cap.
RUN_CHECK_TIMEOUT_SEC = 60
RUN_CHECK_OUTPUT_CHAR_LIMIT = 12_000
RUN_CHECK_MAX_ARGS = 8
RUN_CHECK_ARG_MAX = 200
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
    "Ask-first is locked (cos/prompts/ask-first.md). "
    "If the current directive is a question, statement, or observation, "
    "do not call write_file, update_task, handoff, or git add/commit/push. "
    "You may call list_dir, read_file, file_info, file_outline, "
    "read_file_range, read_file_head, read_file_tail, search_in_file, "
    "search_files, list_files, patch_file, and run_check "
    "only when a read is required to answer. "
    "patch_file is a write. run_check runs a script. Do not call either while discussing. "
    "Do not start implied backlog work. Open tasks are not a work order. "
    "Tools for this turn: list_dir(path), read_file(path), write_file(path, content), "
    "update_task(id, status, title, note), git(action, paths, message, remote, branch), "
    "handoff(task), file_info(path), file_outline(path), "
    "read_file_range(path, start_line, end_line), read_file_head(path, n), "
    "read_file_tail(path, n), search_in_file(path, pattern, context_lines), "
    "search_files(pattern, path, context_lines), "
    "list_files(path, glob, recursive), "
    "patch_file(path, search, replace, replace_all), "
    "run_check(path, args). "
    "file_info, file_outline, the partial reads, search_files, list_files, patch_file, and run_check are additive. "
    "They do not replace read_file or write_file. "
    "patch_file is one file, one literal search. "
    "If replace_all is false and the search is not unique, it errors and does not write. "
    "A missing search does not write. It does not update cos/tasks.md. "
    "It is not apply_patches, not replace_range, and not a multi-file write. "
    "Prefer file_info, file_outline, or a partial read before a full read of a large file. "
    "read_file_range is inclusive and 1-based. An out-of-range request errors; it does not return the whole file. "
    "search_in_file searches one file only. It is not search_files. "
    "search_files is a literal in-workspace search. It is not regex and not search_in_file. "
    "list_files is a simple glob listing. Recursion is off unless asked. "
    "Neither replaces list_dir. Neither expands .git. "
    "Use list_dir first when you need to see what files exist; do not guess paths. "
    "list_dir path is workspace-relative; pass '.' for the workspace root. "
    "It returns workspace-relative paths you can pass to read_file. "
    "It does not follow symlinks, does not expand .git, and is depth- and entry-capped. "
    "Use read_file and write_file to inspect and change files, and handoff to emit a Claude hand-off. "
    "Open work is in cos/tasks.md (also shown below when present). "
    "Use update_task to add or change a task; do not rewrite cos/tasks.md with write_file or patch_file. "
    "Statuses: open, doing, blocked, done. At most one task may be doing. "
    "Mark a task doing before you start it and done only after the file change is written. "
    "Do that only when the current directive explicitly told you to do the work. "
    "Git is a bounded tool, not a shell. Allowed actions: status, diff, log, add, commit, push. "
    "status, diff, and log need no approval. add stages explicit workspace paths only. "
    "commit and push run only when the current user directive contains the exact phrase "
    "'approve git commit' or 'approve git push'. A prior session approval does not count. "
    "Do not invent that phrase. If it is absent, report the status and the phrase required; do not commit or push. "
    "Never pass extra git flags. No force, no reset, no clean, no config, no commit --amend. "
    "At most one commit and one push per user turn. "
    "After a successful push, stop. Do not add, commit, status, or push again in the same turn. "
    "Logging rewrites cos/context.md and cos/event_log.jsonl after every git action, "
    "so a clean tree after push is impossible inside the same turn. "
    "Leave that residual dirty state for the next directive. "
    "Do not stage protocol/test.txt, protocol/overflow_test.py, protocol/step2_self_test.py, "
    "protocol/step3_self_test.py, protocol/_fix_search_ctx.py, or cos/file_info.py. "
    "You may call tools across multiple rounds; results are returned to you in this turn. "
    "The tool loop is capped at 40 rounds. Prefer a final answer before the cap. "
    "The tool-result character cap stays off until partial IO is verified with the user. "
    "Do not restore the 12,000-character cap in this step. "
    "read_file returns the full file. "
    "A result that still says TRUNCATED is not the whole text. "
    "Do not claim you cannot read or list local files. "
    "Do not emit manual CLI lines (list:, read:, write:, improve:, handoff:, tasks); "
    "those are user commands, not your interface. "
    "Do not run shell or network actions other than the git tool and run_check; only the provided tools. "
    "run_check is python plus one workspace script. It is not a shell, not run_command, and not git. "
    "It refuses flags, absolute paths, '..', and anything under cos/. Timeout is 60 seconds. "
    "A failed check does not write files by itself. Do not stage the check script. "
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
                "Path must stay inside the workspace (example: cos/cos.py, specs/META-COS.md). "
                "Full file on disk. The tool-result character cap stays off until partial IO "
                "is verified, so the text returned is the full file. "
                "If a result still says TRUNCATED, it is not the whole file. "
                "Prefer file_info, file_outline, or a partial read for a large file. "
                "Partial reads do not replace this tool."
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
                "At most one commit and one push per user turn. "
                "A successful push ends the turn; do not chase a clean tree after it. "
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
    {
        "type": "function",
        "function": {
            "name": "file_info",
            "description": (
                "Return size_bytes, line_count, mtime_iso, and sha256 for one workspace file. "
                "Does not return file content. Path must stay inside the workspace. "
                "Use this before read_file when the file may be large. "
                "Does not replace read_file."
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
            "name": "file_outline",
            "description": (
                "Return a structural summary of one workspace file. "
                "Markdown: headings with line numbers. JSON: top-level keys. "
                "Python and Kotlin: class and function names with line numbers. "
                "Other text: first and last few lines. Does not return the file body. "
                "Path must stay inside the workspace. Does not replace read_file."
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
            "name": "read_file_range",
            "description": (
                "Read an inclusive 1-based line range from one workspace text file. "
                "Returns those lines plus 'lines X-Y of Z'. "
                "Does not return the whole file. An out-of-range or inverted range errors. "
                "A range wider than 400 lines errors. Path must stay inside the workspace. "
                "Does not replace read_file. Not search_files. Not patch_file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Workspace-relative file path.",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "First line to return. 1-based. Inclusive.",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Last line to return. 1-based. Inclusive.",
                    },
                },
                "required": ["path", "start_line", "end_line"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file_head",
            "description": (
                "Read the first n lines of one workspace text file. Default n is 40. "
                "Max n is 400. Returns those lines plus 'lines X-Y of Z'. "
                "Does not replace read_file. Path must stay inside the workspace."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Workspace-relative file path.",
                    },
                    "n": {
                        "type": "integer",
                        "description": "Number of lines from the start. Default 40. Max 400.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file_tail",
            "description": (
                "Read the last n lines of one workspace text file. Default n is 40. "
                "Max n is 400. Returns those lines plus 'lines X-Y of Z'. "
                "Does not replace read_file. Path must stay inside the workspace."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Workspace-relative file path.",
                    },
                    "n": {
                        "type": "integer",
                        "description": "Number of lines from the end. Default 40. Max 400.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_in_file",
            "description": (
                "Search one workspace text file for a literal pattern. "
                "Returns matching lines with context_lines of context (default 2, max 5). "
                "Does not search other files. Does not use regex. "
                "Path must stay inside the workspace. Does not replace read_file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Workspace-relative file path.",
                    },
                    "pattern": {
                        "type": "string",
                        "description": "Literal text to find. Not a regex. Max 200 characters.",
                    },
                    "context_lines": {
                        "type": "integer",
                        "description": "Lines of context before and after each match. Default 2. Max 5.",
                    },
                },
                "required": ["path", "pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": (
                "Search workspace text files for a literal pattern. "
                "Returns matching lines with context_lines of context (default 2, max 5). "
                "path defaults to '.'. Recursion is off unless recursive is true. "
                "Does not use regex. Does not expand .git, __pycache__, .venv, "
                "node_modules, or .idea. Does not replace search_in_file or list_dir. "
                "Not patch_file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Literal text to find. Not a regex. Max 200 characters.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Workspace-relative file or directory. Default '.' .",
                    },
                    "context_lines": {
                        "type": "integer",
                        "description": "Lines of context before and after each match. Default 2. Max 5.",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "Search subdirectories. Default false.",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": (
                "List workspace files matching a simple glob. "
                "path defaults to '.'. glob defaults to '*'. "
                "Recursion is off unless recursive is true. "
                "Returns workspace-relative paths. Does not follow symlinks. "
                "Does not expand .git, __pycache__, .venv, node_modules, or .idea. "
                "Does not replace list_dir. Not search_files. Not patch_file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Workspace-relative directory or file. Default '.' .",
                    },
                    "glob": {
                        "type": "string",
                        "description": "Simple glob: *, ?, and character classes. Default '*'. Not a regex.",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "Include subdirectories. Default false.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "patch_file",
            "description": (
                "Literal search/replace in one workspace text file. "
                "If replace_all is false and the search occurs any number other than 1, "
                "return an error and do not write. A missing search does not write. "
                "Returns a short diff summary. Does not replace write_file. "
                "Does not update cos/tasks.md. Not apply_patches. Not a multi-file write. "
                "Keeps the file newline style. Does not turn CRLF into CR CR LF."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Workspace-relative file path. Not cos/tasks.md.",
                    },
                    "search": {
                        "type": "string",
                        "description": "Literal text to find. Not a regex. Must be non-empty.",
                    },
                    "replace": {
                        "type": "string",
                        "description": "Literal replacement. May be empty.",
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace every occurrence. Default false. False requires exactly one match.",
                    },
                },
                "required": ["path", "search", "replace"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_check",
            "description": (
                "Run one workspace Python script as a bounded check. "
                "Command shape is python plus one workspace-relative script path "
                "and optional args that are not flags. "
                "shell=False. No cmd, no PowerShell, no git, no network. "
                "Timeout 60 seconds. Stdout and stderr are each capped at 12,000 characters. "
                "Refuses absolute paths, '..', and anything under cos/. "
                "Does not stage the script. Does not commit. Not a shell. Not run_command."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Workspace-relative script path. Not under cos/. No absolute path. No '..'.",
                    },
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional args. Not flags. No leading dash. Max 8. Each max 200 characters.",
                    },
                },
                "required": ["path"],
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

# Per-turn git budget. Reset at the start of each tool loop. A successful
# push sets stop_after_push so the loop returns without another model round.
_TURN_GIT = {
    "commits": 0,
    "pushes": 0,
    "stop_after_push": False,
}


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
    for line in text.splitlines():
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
            for i, line in enumerate(wrapped.splitlines(), 1):
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



def _reload_tools():
    """Load cos/tools_impl.py from disk and bind this module's globals.

    Uses a file location, not import cos.tools_impl, so it works both when
    this file is __main__ (python cos/cos.py) and when it is imported.
    A failed load returns the last good module. It does not drop tools.
    """
    import importlib.util
    path = Path(__file__).resolve().parent / "tools_impl.py"
    name = "cos_tools_impl_live"
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError("cannot load cos/tools_impl.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.bind(sys.modules[__name__])
        sys.modules[name] = mod
        return mod
    except Exception as e:
        emit(f"[tool] reload tools_impl failed: {e}")
        try:
            log_event(
                "tool_reload",
                f"tools_impl reload failed: {e}",
                detail={"error": _clip(str(e), 300)},
            )
        except Exception:
            pass
        return sys.modules.get(name)


def _impl_call(name, *args, **kwargs):
    impl = _reload_tools()
    if impl is None:
        raise RuntimeError("cos/tools_impl.py is not loaded")
    fn = getattr(impl, name, None)
    if not callable(fn):
        raise RuntimeError(f"tool function missing after reload: {name}")
    return fn(*args, **kwargs)


def update_task(*args, **kwargs):
    return _impl_call("update_task", *args, **kwargs)


def read_file(*args, **kwargs):
    return _impl_call("read_file", *args, **kwargs)


def write_file(*args, **kwargs):
    return _impl_call("write_file", *args, **kwargs)


def list_dir(*args, **kwargs):
    return _impl_call("list_dir", *args, **kwargs)


def run_git(*args, **kwargs):
    return _impl_call("run_git", *args, **kwargs)



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


def load_ask_first() -> str:
    """Return cos/prompts/ask-first.md. Re-read each call. Never raises.

    Not cached, so an edit is visible on the next prompt build after this
    loader is running. A missing file still states the rule.
    """
    fallback = (
        "(cos/prompts/ask-first.md missing; ask-first still applies: "
        "questions and statements are not permission to act)"
    )
    try:
        if not POLICY.is_file():
            return fallback
        text = POLICY.read_text(encoding="utf-8")
        if not text.strip():
            return fallback
        return text
    except OSError as e:
        return f"(unable to load cos/prompts/ask-first.md: {e}; ask-first still applies)"
    except UnicodeDecodeError as e:
        return f"(unable to decode cos/prompts/ask-first.md: {e}; ask-first still applies)"


def build_system_prompt(extra: str = None) -> str:
    spec_text = load_meta_cos().rstrip()
    policy_text = load_ask_first().rstrip()
    system = (
        "You are the local Chief of Staff (COS) described in META-COS.md. "
        "Follow its principles: spec-driven, hierarchical, inspectable, "
        "self-improving, and local-first. Grok is the default executor. "
        "Be concise and actionable. When asked to improve a file, return ONLY "
        "the complete new file content, no markdown fences, no commentary.\n\n"
        "=== specs/META-COS.md ===\n"
        f"{spec_text}\n"
        "=== end specs/META-COS.md ===\n\n"
        "=== cos/prompts/ask-first.md ===\n"
        f"{policy_text}\n"
        "=== end cos/prompts/ask-first.md ===\n\n"
        "Operating rule: ask-first overrides any language that says you are in charge, "
        "should drive progress, or should start the next open task. "
        "Questions and statements are discussion. Act only on an explicit instruction "
        "in the current directive.\n\n"
        "Available commands:\n"
        f"{AVAILABLE_COMMANDS}\n\n"
        "Maintain and use the deep persistent context. "
        "Use tools when the current directive explicitly tells you to do the work, "
        "or use list_dir and read_file when a read is required to answer accurately. "
        "Do not start implied backlog work."
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


def _tool_result_text(text: str, limit: int = None) -> str:
    """Return tool text for the model. Does not change the file on disk.

    TOOL_RESULT_CHAR_LIMIT is None while the cap stays off.
    In that mode the full string is returned. A positive limit still truncates
    and says TRUNCATED. Partial reads stay available either way.
    Do not restore the cap until the user verifies partial IO.
    """
    if not isinstance(text, str):
        text = str(text)
    if limit is None:
        limit = TOOL_RESULT_CHAR_LIMIT
    if limit is None:
        return text
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return text
    if limit < 1:
        return text
    original = len(text)
    if original <= limit:
        return text
    # Fixed-width note so the omitted count cannot push the result over the cap.
    # "omitted N of M" uses the original length. Shown prefix is limit minus note.
    note = (
        "\n[TRUNCATED not whole; omitted %d of %d; "
        "use file_info, file_outline, or a partial read]\n"
    ) % (original - limit, original)
    # If the note itself is longer than the cap, hard-cut the note only.
    if len(note) >= limit:
        return note[:limit]
    # Recompute omitted against the original, then shrink the prefix so
    # prefix + note <= limit. The note's omitted figure stays the original gap
    # (original - limit), which is the policy number, not the fitted gap.
    room = limit - len(note)
    fitted = text[:room] + note
    if len(fitted) > limit:
        fitted = fitted[:limit]
    return fitted


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
    """Run one tool call against cos/tools_impl.py on disk. Never raises.

    Reloads that file before the call. A failure in one tool returns an
    error string. It does not unregister the others.
    """
    if not isinstance(arguments, dict):
        arguments = {}
    label = name or "(unnamed)"
    try:
        impl = _reload_tools()
        if impl is None:
            raise RuntimeError("cos/tools_impl.py is not loaded")

        def fn(attr):
            found = getattr(impl, attr, None)
            if not callable(found):
                raise ValueError(f"unknown tool: {name}")
            return found

        if name == "list_dir":
            rel = str(arguments.get("path") or ".").strip() or "."
            text = fn("list_dir")(rel)
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
            content = fn("read_file")(rel)
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
            fn("write_file")(rel, content)
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
            rendered = fn("update_task")(task_id, status, title, note)
            emit(f"[tool] update_task {task_id} -> {status}")
            log_event(
                "tool_exec",
                f"update_task {task_id} -> {status}",
                detail={"tool": name, "id": task_id, "status": status, "round": round_i},
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
            text = fn("run_git")(action, paths, message, remote, branch)
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
            return "Claude hand-off prompt emitted to the user for task: " + _clip(task, 300)
        if name == "file_info":
            rel = str(arguments.get("path") or "").strip()
            if not rel:
                raise ValueError("path is required")
            text = fn("file_info")(rel)
            emit(f"[tool] file_info {rel}")
            log_event("tool_exec", f"file_info {rel}", detail={"tool": name, "path": rel, "round": round_i})
            return text
        if name == "file_outline":
            rel = str(arguments.get("path") or "").strip()
            if not rel:
                raise ValueError("path is required")
            text = fn("file_outline")(rel)
            nlines = text.count("\n") + (1 if text else 0)
            emit(f"[tool] file_outline {rel} -> {nlines} lines")
            log_event(
                "tool_exec",
                f"file_outline {rel}",
                detail={"tool": name, "path": rel, "lines": nlines, "round": round_i},
            )
            return text
        if name == "read_file_range":
            rel = str(arguments.get("path") or "").strip()
            if not rel:
                raise ValueError("path is required")
            text = fn("read_file_range")(rel, arguments.get("start_line"), arguments.get("end_line"))
            emit(f"[tool] read_file_range {rel}")
            log_event("tool_exec", f"read_file_range {rel}", detail={"tool": name, "path": rel, "round": round_i})
            return text
        if name == "read_file_head":
            rel = str(arguments.get("path") or "").strip()
            if not rel:
                raise ValueError("path is required")
            text = fn("read_file_head")(rel, arguments.get("n", PARTIAL_READ_DEFAULT_N))
            emit(f"[tool] read_file_head {rel}")
            log_event("tool_exec", f"read_file_head {rel}", detail={"tool": name, "path": rel, "round": round_i})
            return text
        if name == "read_file_tail":
            rel = str(arguments.get("path") or "").strip()
            if not rel:
                raise ValueError("path is required")
            text = fn("read_file_tail")(rel, arguments.get("n", PARTIAL_READ_DEFAULT_N))
            emit(f"[tool] read_file_tail {rel}")
            log_event("tool_exec", f"read_file_tail {rel}", detail={"tool": name, "path": rel, "round": round_i})
            return text
        if name == "search_in_file":
            rel = str(arguments.get("path") or "").strip()
            pattern = arguments.get("pattern")
            if not rel:
                raise ValueError("path is required")
            if not isinstance(pattern, str):
                raise ValueError("pattern is required")
            text = fn("search_in_file")(rel, pattern, arguments.get("context_lines", 2))
            emit(f"[tool] search_in_file {rel}")
            log_event("tool_exec", f"search_in_file {rel}", detail={"tool": name, "path": rel, "round": round_i})
            return text
        if name == "search_files":
            pattern = arguments.get("pattern")
            if not isinstance(pattern, str):
                raise ValueError("pattern is required")
            rel = str(arguments.get("path") or ".").strip() or "."
            text = fn("search_files")(pattern, rel, arguments.get("context_lines", 2), arguments.get("recursive", False))
            emit(f"[tool] search_files {rel}")
            log_event("tool_exec", f"search_files {rel}", detail={"tool": name, "path": rel, "round": round_i})
            return text
        if name == "list_files":
            rel = str(arguments.get("path") or ".").strip() or "."
            glob = arguments.get("glob", "*")
            if glob is None:
                glob = "*"
            if not isinstance(glob, str):
                raise ValueError("glob must be a string")
            text = fn("list_files")(rel, glob, arguments.get("recursive", False))
            emit(f"[tool] list_files {rel}")
            log_event("tool_exec", f"list_files {rel}", detail={"tool": name, "path": rel, "round": round_i})
            return text
        if name == "patch_file":
            rel = str(arguments.get("path") or "").strip()
            search = arguments.get("search")
            replace = arguments.get("replace")
            if not isinstance(search, str):
                raise ValueError("search is required")
            if not isinstance(replace, str):
                raise ValueError("replace is required")
            text = fn("patch_file")(rel, search, replace, arguments.get("replace_all", False))
            emit(f"[tool] patch_file {rel}")
            log_event("tool_exec", f"patch_file {rel}", detail={"tool": name, "path": rel, "round": round_i})
            return text
        if name == "run_check":
            rel = str(arguments.get("path") or "").strip()
            if not rel:
                raise ValueError("path is required")
            text = fn("run_check")(rel, arguments.get("args"))
            emit(f"[tool] run_check {rel}")
            log_event("tool_exec", f"run_check {rel}", detail={"tool": name, "path": rel, "round": round_i})
            return text
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


def _finish_without_tools(messages, headers, context_bytes, truncated, reason: str) -> str:
    """One extra Grok call with tools removed, so a cap still yields an answer.

    Does not count against MAX_TOOL_ROUNDS. If this call fails or still asks
    for tools, return the best text already in hand plus the reason.
    """
    messages = list(messages)
    messages.append({
        "role": "user",
        "content": (
            "Tool rounds are finished (" + reason + "). "
            "Do not call tools. Reply with the concise final answer: "
            "findings, decisions, paths changed, and anything left for the next turn."
        ),
    })
    payload = {
        "model": "grok-4.7",
        "messages": messages,
        "temperature": 0.2,
    }
    try:
        r = requests.post(GROK_URL, headers=headers, json=payload, timeout=600)
        r.raise_for_status()
        message = _message_from_response(r.json())
    except Exception as e:
        log_event(
            "grok_call",
            f"Final-answer call failed: {e}",
            actor="grok",
            refs=["cos/context.md", "cos/event_log.jsonl"],
            detail={
                "error": _clip(str(e), 600),
                "reason": reason,
                "context_bytes": context_bytes,
                "context_truncated": truncated,
            },
            context_bytes=context_bytes,
        )
        return ""
    content = _message_text(message).strip()
    tool_calls = _normalize_tool_calls(message.get("tool_calls"))
    log_event(
        "grok_call",
        "Final-answer call completed",
        actor="grok",
        refs=["cos/context.md", "cos/event_log.jsonl"],
        detail={
            "reason": reason,
            "reply_preview": content[:200],
            "ignored_tool_calls": [tc["function"]["name"] for tc in tool_calls],
            "context_bytes": context_bytes,
            "context_truncated": truncated,
        },
        context_bytes=context_bytes,
    )
    if tool_calls and not content:
        return ""
    return content


def ask_grok_with_tools(user_input: str) -> str:
    """Call Grok with tools until a final answer, a push, or the round cap.

    The cap is 40 tool rounds. Hitting it triggers one no-tools summary call
    so the user still gets an answer. A successful push ends the turn without
    another model round, because logging would dirty the tree again.
    """
    global _TURN_DIRECTIVE, _TURN_GIT
    _TURN_DIRECTIVE = user_input or ""
    _TURN_GIT = {"commits": 0, "pushes": 0, "stop_after_push": False}
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
    stopped_after_push = False
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

            if _TURN_GIT.get("stop_after_push"):
                stopped_after_push = True
                emit("[tool] push succeeded; ending turn")
                log_event(
                    "tool_stop",
                    "Ended turn after successful push",
                    detail={"rounds": rounds_used, "tools": tools_used},
                )
                note = (
                    "Push succeeded. Turn stopped so logging would not be "
                    "committed again. cos/context.md and cos/event_log.jsonl "
                    "will still be dirty; that residual belongs to the next directive."
                )
                if content and content.strip():
                    final_text = content.strip() + "\n\n" + note
                else:
                    final_text = note
                break
        else:
            stopped_at_limit = True
            emit(f"[tool] max rounds ({MAX_TOOL_ROUNDS}) reached; requesting final answer")
            log_event(
                "tool_limit",
                f"Stopped after {MAX_TOOL_ROUNDS} tool rounds",
                detail={"rounds": MAX_TOOL_ROUNDS, "tools": tools_used},
            )
            summary_text = _finish_without_tools(
                messages,
                headers,
                context_bytes,
                truncated,
                f"hit the {MAX_TOOL_ROUNDS}-round tool cap",
            )
            note = (
                f"Stopped after {MAX_TOOL_ROUNDS} tool rounds. "
                "Unfinished work stays on the task list."
            )
            if summary_text:
                final_text = summary_text
            elif content and content.strip():
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
        _TURN_GIT = {"commits": 0, "pushes": 0, "stop_after_push": False}

    log_event(
        "tool_loop",
        f"Tool loop finished in {rounds_used} round(s)",
        detail={
            "rounds": rounds_used,
            "tools": tools_used,
            "max_rounds": MAX_TOOL_ROUNDS,
            "stopped_at_limit": stopped_at_limit,
            "stopped_after_push": stopped_after_push,
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
    emit("One commit and one push per turn. A successful push ends the turn.")
    emit("Ask-first is locked: questions and statements are not permission to act.")
    emit("Tool-result character cap stays off until partial IO is verified.")
    emit("Partial reads: read_file_range, read_file_head, read_file_tail, search_in_file.")
    emit("Cross-file: search_files, list_files. Recursion off unless asked. .git not expanded.")
    emit("run_check: python, one workspace script, no flags, no shell. Not under cos/.")


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
    policy_text = load_ask_first()
    ensure_context_file()
    ensure_tasks_file()
    emit("COS ready.")
    emit(f"Spec exists: {SPEC.exists()}")
    ctx_bytes = _file_size(CONTEXT)
    emit(f"Deep context file: cos/context.md ({ctx_bytes} bytes)")
    task_bytes = _file_size(TASKS)
    emit(f"Task list: cos/tasks.md ({task_bytes} bytes)")
    policy_bytes = _file_size(POLICY)
    emit(f"Ask-first policy: cos/prompts/ask-first.md ({policy_bytes} bytes)")
    log_event(
        "startup",
        "COS started; specs/META-COS.md and ask-first policy loaded into system prompt",
        refs=["specs/META-COS.md", "cos/prompts/ask-first.md", "cos/context.md", "cos/tasks.md"],
        detail={
            "bytes": len(spec_text.encode("utf-8")),
            "policy_bytes": len(policy_text.encode("utf-8")),
            "policy_file_bytes": policy_bytes,
            "context_file_bytes": ctx_bytes,
            "tasks_file_bytes": task_bytes,
            "tool_result_char_limit": TOOL_RESULT_CHAR_LIMIT,
            "tools": [t["function"]["name"] for t in TOOLS],
        },
    )
    append_context(
        "- Session start: COS started. Full META-COS.md loaded into the system prompt. "
        "Ask-first policy loads from cos/prompts/ask-first.md. "
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
                    new_content = "\n".join(new_content.splitlines()[1:-1])
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
