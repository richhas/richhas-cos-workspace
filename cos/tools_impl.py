"""Tool implementations loaded from disk on each COS tool call.

Not an entry point. cos/cos.py loads this file before each call and binds
the running module's globals in. A write here is visible on the next call.
A write to cos/cos.py still needs one restart. Do not run this file.
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

EDGE_LINES = 8
EDGE_CHARS = 120
PARTIAL_READ_DEFAULT_N = 40


def bind(host):
    """Copy host globals that this module does not define.

    Called after every load. Keeps this module's functions. Shares mutable
    host state such as _TURN_GIT by object reference.
    """
    g = globals()
    own = set(g)
    src = vars(host)
    for name, value in src.items():
        if name.startswith("__"):
            continue
        if name in own:
            continue
        g[name] = value


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


def _outline_line(text: str, limit: int = 120) -> str:
    return _one_line(text, limit)


def _line_count_bytes(data: bytes) -> int:
    """Line count matching str.splitlines() for UTF-8 text bytes."""
    if not data:
        return 0
    n = data.count(b"\n") + data.count(b"\r") - data.count(b"\r\n")
    if not data.endswith((b"\n", b"\r")):
        n += 1
    return n


def file_info(rel_path: str) -> str:
    """Return size, line count, mtime, and sha256. Does not return content.

    Raises on escape, missing path, or a directory. Does not replace read_file.
    """
    if not isinstance(rel_path, str) or not rel_path.strip():
        raise ValueError("path is required")
    raw = rel_path.strip().replace("\\", "/")
    target = resolve_safe(raw)
    if not target.exists():
        raise FileNotFoundError(raw)
    if not target.is_file():
        raise ValueError(f"Not a file: {raw}")
    data = target.read_bytes()
    try:
        mtime = datetime.fromtimestamp(target.stat().st_mtime, timezone.utc)
        mtime_iso = mtime.isoformat()
    except OSError:
        mtime_iso = ""
    shown = raw.lstrip("./") or raw
    text = "\n".join([
        f"path: {shown}",
        f"size_bytes: {len(data)}",
        f"line_count: {_line_count_bytes(data)}",
        f"mtime_iso: {mtime_iso}",
        f"sha256: {hashlib.sha256(data).hexdigest()}",
    ])
    log_event(
        "file_info",
        f"Info {raw}",
        detail={"path": raw, "bytes": len(data)},
    )
    return text


def _markdown_items(text: str):
    items = []
    truncated = False
    for i, line in enumerate(text.splitlines(), 1):
        if i > OUTLINE_MAX_LINES:
            truncated = True
            break
        match = _MD_HEADING.match(line)
        if not match:
            continue
        level = len(match.group(1))
        title = _outline_line(match.group(2).strip(), 100)
        items.append(f"L{i} {'#' * level} {title}")
        if len(items) >= OUTLINE_MAX_ITEMS:
            truncated = True
            break
    return items, truncated


def _json_items(text: str):
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return [f"note: JSON parse failed: {e}"], False
    if isinstance(data, dict):
        keys = list(data.keys())
        items = [f"key: {_outline_line(str(k), 80)}" for k in keys[:OUTLINE_MAX_ITEMS]]
        return items, len(keys) > OUTLINE_MAX_ITEMS
    if isinstance(data, list):
        return [f"top-level: array length {len(data)}"], False
    return [f"top-level: {type(data).__name__}"], False


def _code_items(text: str, suffix: str):
    items = []
    truncated = False
    use_kt = suffix in (".kt", ".kts")
    for i, line in enumerate(text.splitlines(), 1):
        if i > OUTLINE_MAX_LINES:
            truncated = True
            break
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#") or stripped.startswith("//"):
            continue
        kind = ""
        name = ""
        indent = 0
        if use_kt:
            cm = _KT_CLASS.match(line)
            fm = _KT_FUN.match(line)
            if cm:
                kind = cm.group(2)
                name = cm.group(3)
                indent = len(cm.group(1).replace("\t", "    "))
            elif fm:
                kind = "fun"
                name = fm.group(2)
                indent = len(fm.group(1).replace("\t", "    "))
        else:
            cm = _PY_CLASS.match(line)
            fm = _PY_DEF.match(line)
            if cm:
                kind = "class"
                name = cm.group(2)
                indent = len(cm.group(1).replace("\t", "    "))
            elif fm:
                kind = "def"
                name = fm.group(2)
                indent = len(fm.group(1).replace("\t", "    "))
        if not kind:
            continue
        depth = min(indent // 4, 6)
        items.append(f"L{i} {'  ' * depth}{kind} {name}")
        if len(items) >= OUTLINE_MAX_ITEMS:
            truncated = True
            break
    return items, truncated


def _edge_items(text: str):
    rows = text.splitlines()
    total = len(rows)
    if total == 0:
        return ["(empty file)"]
    items = [f"first L{i}: {_outline_line(line)}" for i, line in enumerate(rows[:EDGE_LINES], 1)]
    if total > EDGE_LINES:
        tail = rows[-EDGE_LINES:]
        start = total - len(tail) + 1
        if start <= EDGE_LINES:
            tail = rows[EDGE_LINES:]
            start = EDGE_LINES + 1
        if tail:
            items.append("---")
            items.extend(
                f"last L{start + j}: {_outline_line(line)}" for j, line in enumerate(tail)
            )
    items.append(f"line_count: {total}")
    return items


def file_outline(rel_path: str) -> str:
    """Structural summary. Does not return the file body. Does not replace read_file.

    Markdown headings, JSON top-level keys, Python/Kotlin names, or edge lines.
    Raises on escape, missing path, or a directory.
    """
    if not isinstance(rel_path, str) or not rel_path.strip():
        raise ValueError("path is required")
    raw = rel_path.strip().replace("\\", "/")
    target = resolve_safe(raw)
    if not target.exists():
        raise FileNotFoundError(raw)
    if not target.is_file():
        raise ValueError(f"Not a file: {raw}")
    shown = raw.lstrip("./") or raw
    suffix = target.suffix.lower()
    try:
        size = target.stat().st_size
    except OSError as e:
        raise ValueError(f"cannot stat file: {e}") from e
    header = [f"path: {shown}"]
    if size > OUTLINE_MAX_BYTES:
        header.append("kind: skipped")
        header.append(
            f"file is {size} bytes; outline skipped above {OUTLINE_MAX_BYTES} bytes. "
            "Use file_info or a partial read. Full read_file still works. "
            "The tool-result cap stays off until partial IO is verified."
        )
        log_event("file_outline", f"Outline skipped {raw}", detail={"path": raw, "bytes": size})
        return "\n".join(header)
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        header.append("kind: skipped")
        header.append("not valid UTF-8; outline skipped. Use file_info.")
        log_event("file_outline", f"Outline skipped {raw}", detail={"path": raw, "reason": "utf-8"})
        return "\n".join(header)
    except OSError as e:
        raise ValueError(f"cannot read file: {e}") from e

    if suffix == ".md":
        kind = "markdown"
        items, truncated = _markdown_items(text)
    elif suffix == ".json":
        kind = "json"
        items, truncated = _json_items(text)
    elif suffix in (".py", ".kt", ".kts"):
        kind = "kotlin" if suffix in (".kt", ".kts") else "python"
        items, truncated = _code_items(text, suffix)
    else:
        kind = "text"
        items = _edge_items(text)
        truncated = False
    if not items:
        items = ["(no structural items)"]
    header.append(f"kind: {kind}")
    header.append(f"items: {len(items)}")
    if truncated:
        header.append(
            f"truncated: yes (cap {OUTLINE_MAX_ITEMS} items or {OUTLINE_MAX_LINES} lines)"
        )
    log_event(
        "file_outline",
        f"Outline {raw}",
        detail={"path": raw, "kind": kind, "items": len(items), "truncated": truncated},
    )
    return "\n".join(header + items)


def _normalize_rel_path(rel_path: str) -> str:
    if not isinstance(rel_path, str) or not rel_path.strip():
        raise ValueError("path is required")
    return rel_path.strip().replace("\\", "/")


def _read_text_lines(rel_path: str):
    """Return (shown_path, lines) for one workspace text file. Raises on bad path.

    Uses splitlines() so CRLF and LF agree. Does not follow a path outside
    the workspace. A directory is an error. Does not replace read_file.
    """
    raw = _normalize_rel_path(rel_path)
    target = resolve_safe(raw)
    if not target.exists():
        raise FileNotFoundError(raw)
    if not target.is_file():
        raise ValueError(f"Not a file: {raw}")
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"not valid UTF-8: {raw}") from e
    except OSError as e:
        raise ValueError(f"cannot read file: {e}") from e
    shown = raw.lstrip("./") or raw
    return shown, text.splitlines()


def _parse_positive_int(value, label: str) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{label} must be a positive integer")
    if isinstance(value, int):
        n = value
    elif isinstance(value, str) and value.strip().isdigit():
        n = int(value.strip())
    else:
        raise ValueError(f"{label} must be a positive integer")
    if n < 1:
        raise ValueError(f"{label} must be >= 1")
    return n


def _format_line_span(lines, start: int, end: int, total: int) -> str:
    """Format an inclusive 1-based span. start and end are already validated."""
    body = []
    for n in range(start, end + 1):
        body.append(f"{n}|{lines[n - 1]}")
    header = f"lines {start}-{end} of {total}"
    if not body:
        return header
    return header + "\n" + "\n".join(body)


def read_file_range(rel_path: str, start_line, end_line) -> str:
    """Inclusive 1-based line range. Errors instead of reading the whole file.

    Does not replace read_file. A span wider than PARTIAL_READ_MAX_LINES errors.
    """
    shown, lines = _read_text_lines(rel_path)
    total = len(lines)
    start = _parse_positive_int(start_line, "start_line")
    end = _parse_positive_int(end_line, "end_line")
    if start > end:
        raise ValueError(f"start_line {start} is after end_line {end}")
    if end - start + 1 > PARTIAL_READ_MAX_LINES:
        raise ValueError(
            f"range is {end - start + 1} lines; max is {PARTIAL_READ_MAX_LINES}. "
            "Narrow the range. This does not read the whole file."
        )
    if total == 0 or start > total or end > total:
        raise ValueError(
            f"range {start}-{end} is outside 1-{total}. "
            "Refusing to read the whole file."
        )
    text = _format_line_span(lines, start, end, total)
    log_event(
        "file_read_range",
        f"Range {shown} {start}-{end} of {total}",
        detail={"path": shown, "start": start, "end": end, "total": total},
    )
    return text


def read_file_head(rel_path: str, n=40) -> str:
    """First n lines. Default 40. Does not replace read_file."""
    if n is None:
        n = PARTIAL_READ_DEFAULT_N
    count = _parse_positive_int(n, "n")
    if count > PARTIAL_READ_MAX_LINES:
        raise ValueError(f"n is {count}; max is {PARTIAL_READ_MAX_LINES}")
    shown, lines = _read_text_lines(rel_path)
    total = len(lines)
    if total == 0:
        text = "lines 0-0 of 0"
    else:
        end = min(count, total)
        text = _format_line_span(lines, 1, end, total)
    log_event(
        "file_read_head",
        f"Head {shown} n={count}",
        detail={"path": shown, "n": count, "total": total},
    )
    return text


def read_file_tail(rel_path: str, n=40) -> str:
    """Last n lines. Default 40. Does not replace read_file."""
    if n is None:
        n = PARTIAL_READ_DEFAULT_N
    count = _parse_positive_int(n, "n")
    if count > PARTIAL_READ_MAX_LINES:
        raise ValueError(f"n is {count}; max is {PARTIAL_READ_MAX_LINES}")
    shown, lines = _read_text_lines(rel_path)
    total = len(lines)
    if total == 0:
        text = "lines 0-0 of 0"
    else:
        start = max(1, total - count + 1)
        text = _format_line_span(lines, start, total, total)
    log_event(
        "file_read_tail",
        f"Tail {shown} n={count}",
        detail={"path": shown, "n": count, "total": total},
    )
    return text


def _parse_context_lines(value) -> int:
    """context_lines may be 0. Default 2. Max SEARCH_IN_FILE_MAX_CONTEXT."""
    if value is None:
        value = 2
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("context_lines must be an integer")
    if isinstance(value, str):
        raw = value.strip()
        if not raw.isdigit():
            raise ValueError("context_lines must be an integer >= 0")
        ctx = int(raw)
    else:
        ctx = value
    if ctx < 0:
        raise ValueError("context_lines must be >= 0")
    if ctx > SEARCH_IN_FILE_MAX_CONTEXT:
        raise ValueError(f"context_lines is {ctx}; max is {SEARCH_IN_FILE_MAX_CONTEXT}")
    return ctx


def search_in_file(rel_path: str, pattern: str, context_lines=2) -> str:
    """Literal search inside one file. Returns matching lines with context.

    Not regex. Not search_files. Does not replace read_file.
    """
    if not isinstance(pattern, str) or pattern == "":
        raise ValueError("pattern is required")
    if len(pattern) > SEARCH_IN_FILE_MAX_PATTERN:
        raise ValueError(
            f"pattern is {len(pattern)} characters; max is {SEARCH_IN_FILE_MAX_PATTERN}"
        )
    ctx = _parse_context_lines(context_lines)
    shown, lines = _read_text_lines(rel_path)
    total = len(lines)
    hits = [i for i, line in enumerate(lines, 1) if pattern in line]
    omitted = 0
    if len(hits) > SEARCH_IN_FILE_MAX_MATCHES:
        omitted = len(hits) - SEARCH_IN_FILE_MAX_MATCHES
        hits = hits[:SEARCH_IN_FILE_MAX_MATCHES]
    spans = []
    for hit in hits:
        start = max(1, hit - ctx)
        end = min(total, hit + ctx)
        if spans and start <= spans[-1][1] + 1:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))
    header = [
        f"path: {shown}",
        f"pattern: {pattern}",
        f"matches: {len(hits) + omitted}",
        f"shown: {len(hits)}",
        f"context_lines: {ctx}",
    ]
    if omitted:
        header.append(
            f"truncated: yes; omitted {omitted} matches past {SEARCH_IN_FILE_MAX_MATCHES}"
        )
    if not hits:
        header.append("no matches")
        text = "\n".join(header)
    else:
        blocks = [_format_line_span(lines, start, end, total) for start, end in spans]
        text = "\n".join(header) + "\n" + "\n---\n".join(blocks)
    log_event(
        "file_search",
        f"Search {shown} matches={len(hits) + omitted}",
        detail={
            "path": shown,
            "matches": len(hits) + omitted,
            "shown": len(hits),
            "context_lines": ctx,
        },
    )
    return text


def _parse_bool(value, label: str, default: bool = False) -> bool:
    """Accept a real bool. Missing means default. Strings are not accepted."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise ValueError(f"{label} must be a boolean")


def _simple_glob_match(name: str, pattern: str) -> bool:
    """Match one file name against a simple glob. Not a path glob. Not regex.

    Supports *, ?, and [] classes. ** is rejected by the caller.
    """
    if pattern is None:
        pattern = "*"
    if not isinstance(pattern, str) or pattern.strip() == "":
        raise ValueError("glob must be a non-empty string")
    pattern = pattern.strip()
    if "/" in pattern or "\\" in pattern or "**" in pattern:
        raise ValueError("glob matches a file name only; no path separators")
    return _glob_match_at(name, pattern, 0, 0)


def _glob_match_at(name: str, pattern: str, ni: int, pi: int) -> bool:
    while pi < len(pattern):
        tok = pattern[pi]
        if tok == "*":
            pi += 1
            if pi == len(pattern):
                return True
            while ni <= len(name):
                if _glob_match_at(name, pattern, ni, pi):
                    return True
                ni += 1
            return False
        if ni >= len(name):
            return False
        if tok == "?":
            ni += 1
            pi += 1
            continue
        if tok == "[":
            end = pattern.find("]", pi + 1)
            if end < 0:
                raise ValueError("glob has an unclosed character class")
            if _glob_class_match(name[ni], pattern[pi + 1:end]):
                ni += 1
                pi = end + 1
                continue
            return False
        if name[ni] != tok:
            return False
        ni += 1
        pi += 1
    return ni == len(name)


def _glob_class_match(ch: str, body: str) -> bool:
    if body == "":
        return False
    negate = body[0] == "!"
    if negate:
        body = body[1:]
    matched = False
    i = 0
    while i < len(body):
        if i + 2 < len(body) and body[i + 1] == "-":
            if body[i] <= ch <= body[i + 2]:
                matched = True
                break
            i += 3
            continue
        if body[i] == ch:
            matched = True
            break
        i += 1
    return (not matched) if negate else matched


def _skip_expand_dir(path: Path) -> bool:
    return path.name in _LIST_DIR_SKIP_EXPAND


def _iter_workspace_files(start: Path, recursive: bool):
    """Yield files under start. Does not follow symlinks. Does not expand .git.

    start is already resolved and inside the workspace. A file yields itself.
    """
    if start.is_symlink():
        return
    if start.is_file():
        yield start
        return
    if not start.is_dir():
        return
    if _skip_expand_dir(start) and start != ROOT.resolve():
        return
    stack = [start]
    seen = 0
    while stack:
        directory = stack.pop()
        try:
            children = list(directory.iterdir())
        except OSError:
            continue
        children.sort(key=lambda p: p.name.lower())
        subdirs = []
        for child in children:
            seen += 1
            if seen > SEARCH_FILES_MAX_FILES * 8:
                return
            try:
                if child.is_symlink():
                    continue
                resolved = child.resolve()
                root = ROOT.resolve()
                if resolved != root and root not in resolved.parents:
                    continue
                if child.is_file():
                    yield child
                elif child.is_dir() and recursive and not _skip_expand_dir(child):
                    subdirs.append(child)
            except OSError:
                continue
        if recursive:
            stack.extend(reversed(subdirs))


def search_files(pattern: str, rel_path: str = ".", context_lines=2, recursive=False) -> str:
    """Literal search across workspace text files. Not regex. Not search_in_file.

    Recursion is off unless asked. .git and the other skip dirs are not expanded.
    Does not replace list_dir. Does not write.
    """
    if not isinstance(pattern, str) or pattern == "":
        raise ValueError("pattern is required")
    if len(pattern) > SEARCH_IN_FILE_MAX_PATTERN:
        raise ValueError(
            f"pattern is {len(pattern)} characters; max is {SEARCH_IN_FILE_MAX_PATTERN}"
        )
    ctx = _parse_context_lines(context_lines)
    recursive = _parse_bool(recursive, "recursive", False)
    if not isinstance(rel_path, str) or not rel_path.strip():
        rel_path = "."
    raw = rel_path.strip().replace("\\", "/").rstrip("/") or "."
    target = resolve_safe(raw)
    if not target.exists():
        raise FileNotFoundError(raw)
    root = ROOT.resolve()
    files_seen = 0
    files_searched = 0
    files_skipped = 0
    match_total = 0
    shown = 0
    omitted = 0
    blocks = []
    for path in _iter_workspace_files(target, recursive):
        files_seen += 1
        if files_seen > SEARCH_FILES_MAX_FILES:
            files_skipped += 1
            break
        try:
            size = path.stat().st_size
        except OSError:
            files_skipped += 1
            continue
        if size > SEARCH_FILES_MAX_FILE_BYTES:
            files_skipped += 1
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            files_skipped += 1
            continue
        files_searched += 1
        lines = text.splitlines()
        total = len(lines)
        hits = [i for i, line in enumerate(lines, 1) if pattern in line]
        if not hits:
            continue
        match_total += len(hits)
        room = SEARCH_FILES_MAX_MATCHES - shown
        if room <= 0:
            omitted += len(hits)
            continue
        use = hits[:room]
        omitted += len(hits) - len(use)
        shown += len(use)
        try:
            rel = path.resolve().relative_to(root).as_posix()
        except ValueError:
            rel = path.name
        spans = []
        for hit in use:
            start = max(1, hit - ctx)
            end = min(total, hit + ctx)
            if spans and start <= spans[-1][1] + 1:
                spans[-1] = (spans[-1][0], max(spans[-1][1], end))
            else:
                spans.append((start, end))
        body = "\n---\n".join(
            _format_line_span(lines, start, end, total) for start, end in spans
        )
        blocks.append(f"path: {rel}\n{body}")
    header = [
        f"pattern: {pattern}",
        f"root: {raw}",
        f"recursive: {str(recursive).lower()}",
        f"files_searched: {files_searched}",
        f"files_skipped: {files_skipped}",
        f"matches: {match_total}",
        f"shown: {shown}",
        f"context_lines: {ctx}",
    ]
    if omitted or files_seen > SEARCH_FILES_MAX_FILES:
        header.append(
            f"truncated: yes; omitted {omitted} matches past {SEARCH_FILES_MAX_MATCHES}"
        )
    if not blocks:
        header.append("no matches")
        text = "\n".join(header)
    else:
        text = "\n".join(header) + "\n" + "\n---\n".join(blocks)
    log_event(
        "files_search",
        f"Search {raw} matches={match_total}",
        detail={
            "path": raw,
            "matches": match_total,
            "shown": shown,
            "files_searched": files_searched,
            "recursive": recursive,
        },
    )
    return text


def list_files(rel_path: str = ".", glob: str = "*", recursive=False) -> str:
    """List workspace files matching a simple glob. Does not replace list_dir.

    Recursion is off unless asked. .git is not expanded. Not a regex.
    """
    recursive = _parse_bool(recursive, "recursive", False)
    if glob is None:
        glob = "*"
    if not isinstance(glob, str) or glob.strip() == "":
        raise ValueError("glob must be a non-empty string")
    glob = glob.strip()
    if not isinstance(rel_path, str) or not rel_path.strip():
        rel_path = "."
    raw = rel_path.strip().replace("\\", "/").rstrip("/") or "."
    target = resolve_safe(raw)
    if not target.exists():
        raise FileNotFoundError(raw)
    if target.is_symlink():
        raise ValueError("refusing to follow a symlink")
    root = ROOT.resolve()
    names = []
    truncated = False
    seen = 0
    for path in _iter_workspace_files(target, recursive):
        seen += 1
        if not _simple_glob_match(path.name, glob):
            continue
        if len(names) >= LIST_FILES_MAX_ENTRIES:
            truncated = True
            break
        try:
            rel = path.resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        names.append(rel)
    names.sort()
    header = [
        f"root: {raw}",
        f"glob: {glob}",
        f"recursive: {str(recursive).lower()}",
        f"count: {len(names)}",
    ]
    if truncated:
        header.append(
            f"truncated: yes; stopped at {LIST_FILES_MAX_ENTRIES} matches"
        )
    if not names:
        header.append("no matches")
        text = "\n".join(header)
    else:
        text = "\n".join(header + names)
    log_event(
        "files_list",
        f"Listed files {raw} glob={glob}",
        detail={
            "path": raw,
            "glob": glob,
            "count": len(names),
            "recursive": recursive,
            "truncated": truncated,
        },
    )
    return text


def _short_diff(before: str, after: str, limit: int = 40) -> str:
    """Unified diff, capped. Does not write."""
    import difflib
    lines = list(difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile="before",
        tofile="after",
        lineterm="",
        n=2,
    ))
    if len(lines) > limit:
        omitted = len(lines) - limit
        lines = lines[:limit]
        lines.append(f"... {omitted} diff lines omitted")
    return "\n".join(lines)


def _newline_style(data: bytes) -> str:
    """Return the file's newline. CRLF wins if any CRLF is present."""
    if b"\r\n" in data:
        return "\r\n"
    if b"\r" in data:
        return "\r"
    return "\n"


def _to_lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _from_lf(text: str, newline: str) -> str:
    """Apply newline style to LF text. Does not double an existing CR."""
    if newline == "\n":
        return text
    return text.replace("\n", newline)


def patch_file(rel_path: str, search: str, replace: str, replace_all=False) -> str:
    """Literal search/replace in one file. Unique unless replace_all.

    Does not replace write_file. Does not update cos/tasks.md.
    A missing or non-unique search does not write.
    Search and replace are matched as LF text. The file is written back
    in its original newline style. A CRLF file stays CRLF, not CR CR LF.
    """
    if not isinstance(rel_path, str) or not rel_path.strip():
        raise ValueError("path is required")
    if not isinstance(search, str) or search == "":
        raise ValueError("search must be a non-empty string")
    if not isinstance(replace, str):
        raise ValueError("replace must be a string")
    replace_all = _parse_bool(replace_all, "replace_all", False)
    raw = rel_path.strip().replace("\\", "/").rstrip("/")
    norm = raw.lstrip("./") or raw
    if norm in ("cos/tasks.md", "tasks.md"):
        raise ValueError("use update_task for cos/tasks.md, not patch_file")
    target = resolve_safe(raw)
    if not target.exists():
        raise FileNotFoundError(raw)
    if target.is_symlink():
        raise ValueError("refusing to follow a symlink")
    if not target.is_file():
        raise ValueError(f"Not a file: {raw}")
    original = target.read_bytes()
    newline = _newline_style(original)
    text = _to_lf(original.decode("utf-8"))
    search_lf = _to_lf(search)
    replace_lf = _to_lf(replace)
    count = text.count(search_lf)
    if count == 0:
        raise ValueError("search not found; file not written")
    if not replace_all and count != 1:
        raise ValueError(
            f"search is not unique ({count} matches); file not written"
        )
    if replace_all:
        updated = text.replace(search_lf, replace_lf)
        replaced = count
    else:
        updated = text.replace(search_lf, replace_lf, 1)
        replaced = 1
    if updated == text:
        raise ValueError("replacement did not change the file; file not written")
    payload = _from_lf(updated, newline).encode("utf-8")
    if b"\r\r\n" in payload:
        raise ValueError("refusing to write doubled CR; file not written")
    target.write_bytes(payload)
    diff = _short_diff(text, updated)
    shown = raw.lstrip("./") or raw
    summary = "\n".join([
        f"path: {shown}",
        f"replaced: {replaced}",
        f"size_bytes: {len(payload)}",
        f"newline: {newline!r}",
        "diff:",
        diff,
    ])
    log_event(
        "file_patch",
        f"Patched {raw}",
        detail={"path": raw, "replaced": replaced, "bytes": len(payload)},
    )
    append_context(f"- File change: patched `{raw}` ({replaced} replacement(s))")
    return summary


def append_file(rel_path: str, content: str) -> str:
    """Append text to one workspace file. Creates the file if missing.

    Does not replace write_file or patch_file. Does not update cos/tasks.md.
    Not insert_at_line, not replace_range, not apply_patches.
    Content is matched as LF text and written in the file's newline style.
    A new file uses LF. A CRLF file stays CRLF, not CR CR LF.
    """
    if not isinstance(rel_path, str) or not rel_path.strip():
        raise ValueError("path is required")
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    raw = rel_path.strip().replace("\\", "/").rstrip("/")
    norm = raw.lstrip("./") or raw
    if norm in ("cos/tasks.md", "tasks.md"):
        raise ValueError("use update_task for cos/tasks.md, not append_file")
    target = resolve_safe(raw)
    if target.exists() and target.is_symlink():
        raise ValueError("refusing to follow a symlink")
    if target.exists() and not target.is_file():
        raise ValueError(f"Not a file: {raw}")
    created = not target.exists()
    if created:
        target.parent.mkdir(parents=True, exist_ok=True)
        original = b""
        newline = "\n"
    else:
        original = target.read_bytes()
        newline = _newline_style(original)
    addition = _from_lf(_to_lf(content), newline)
    payload = original + addition.encode("utf-8")
    if b"\r\r\n" in payload:
        raise ValueError("refusing to write doubled CR; file not written")
    target.write_bytes(payload)
    shown = raw.lstrip("./") or raw
    summary = "\n".join([
        f"path: {shown}",
        f"created: {str(created).lower()}",
        f"appended_bytes: {len(addition.encode('utf-8'))}",
        f"size_bytes: {len(payload)}",
        f"newline: {newline!r}",
    ])
    log_event(
        "file_append",
        f"Appended {raw}",
        detail={
            "path": raw,
            "created": created,
            "appended_bytes": len(addition.encode("utf-8")),
            "bytes": len(payload),
        },
    )
    append_context(
        f"- File change: appended `{raw}` ({len(addition.encode('utf-8'))} bytes)"
    )
    return summary


def insert_at_line(rel_path: str, line_number, content: str) -> str:
    """Insert text before a 1-based line. Does not replace the file.

    Does not replace write_file, patch_file, or append_file.
    Does not update cos/tasks.md.
    Not replace_range, not apply_patches.
    An out-of-range line errors and does not write.
    Content is matched as LF text and written in the file's newline style.
    A CRLF file stays CRLF, not CR CR LF.
    """
    if not isinstance(rel_path, str) or not rel_path.strip():
        raise ValueError("path is required")
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    if isinstance(line_number, bool) or not isinstance(line_number, int):
        if isinstance(line_number, str) and line_number.strip().lstrip("-").isdigit():
            line_number = int(line_number.strip())
        else:
            raise ValueError("line_number must be an integer")
    if line_number < 1:
        raise ValueError("line_number must be >= 1; file not written")
    raw = rel_path.strip().replace("\\", "/").rstrip("/")
    norm = raw.lstrip("./") or raw
    if norm in ("cos/tasks.md", "tasks.md"):
        raise ValueError("use update_task for cos/tasks.md, not insert_at_line")
    target = resolve_safe(raw)
    if not target.exists():
        raise FileNotFoundError(raw)
    if target.is_symlink():
        raise ValueError("refusing to follow a symlink")
    if not target.is_file():
        raise ValueError(f"Not a file: {raw}")
    original = target.read_bytes()
    newline = _newline_style(original)
    text = _to_lf(original.decode("utf-8"))
    if text == "":
        lines = []
    else:
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines = lines[:-1]
    if line_number > len(lines) + 1:
        raise ValueError(
            f"line_number {line_number} is past end ({len(lines)} lines); file not written"
        )
    addition = _to_lf(content)
    if addition == "" or addition.endswith("\n"):
        insert_lines = addition.split("\n")
        if insert_lines and insert_lines[-1] == "":
            insert_lines = insert_lines[:-1]
    else:
        insert_lines = [addition]
    updated_lines = lines[: line_number - 1] + insert_lines + lines[line_number - 1 :]
    updated = "\n".join(updated_lines)
    if text.endswith("\n") or text == "":
        updated += "\n"
    if updated == text:
        raise ValueError("insert did not change the file; file not written")
    payload = _from_lf(updated, newline).encode("utf-8")
    if b"\r\r\n" in payload:
        raise ValueError("refusing to write doubled CR; file not written")
    target.write_bytes(payload)
    shown = raw.lstrip("./") or raw
    summary = "\n".join([
        f"path: {shown}",
        f"line_number: {line_number}",
        f"inserted_lines: {len(insert_lines)}",
        f"size_bytes: {len(payload)}",
        f"newline: {newline!r}",
    ])
    log_event(
        "file_insert",
        f"Inserted into {raw}",
        detail={
            "path": raw,
            "line_number": line_number,
            "inserted_lines": len(insert_lines),
            "bytes": len(payload),
        },
    )
    append_context(
        f"- File change: inserted into `{raw}` before line {line_number}"
    )
    return summary


def _as_line_number(value, name: str) -> int:
    """Coerce a tool argument to a 1-based line number. Does not write."""
    if isinstance(value, bool) or not isinstance(value, int):
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            value = int(value.strip())
        else:
            raise ValueError(f"{name} must be an integer")
    if value < 1:
        raise ValueError(f"{name} must be >= 1; file not written")
    return value


def _split_lines(text: str) -> list:
    """Split LF text into lines, dropping one trailing empty line."""
    if text == "":
        return []
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    return lines


def replace_range(rel_path: str, start_line, end_line, new_content: str) -> str:
    """Replace an inclusive 1-based line span. Does not replace the file.

    Does not replace write_file, patch_file, append_file, or insert_at_line.
    Does not update cos/tasks.md.
    Not apply_patches.
    An inverted or out-of-range span errors and does not write.
    Content is matched as LF text and written in the file's newline style.
    A CRLF file stays CRLF, not CR CR LF.
    """
    if not isinstance(rel_path, str) or not rel_path.strip():
        raise ValueError("path is required")
    if not isinstance(new_content, str):
        raise ValueError("new_content must be a string")
    start_line = _as_line_number(start_line, "start_line")
    end_line = _as_line_number(end_line, "end_line")
    if end_line < start_line:
        raise ValueError(
            f"inverted span {start_line}-{end_line}; file not written"
        )
    raw = rel_path.strip().replace("\\", "/").rstrip("/")
    norm = raw.lstrip("./") or raw
    if norm in ("cos/tasks.md", "tasks.md"):
        raise ValueError("use update_task for cos/tasks.md, not replace_range")
    target = resolve_safe(raw)
    if not target.exists():
        raise FileNotFoundError(raw)
    if target.is_symlink():
        raise ValueError("refusing to follow a symlink")
    if not target.is_file():
        raise ValueError(f"Not a file: {raw}")
    original = target.read_bytes()
    newline = _newline_style(original)
    text = _to_lf(original.decode("utf-8"))
    lines = _split_lines(text)
    if start_line > len(lines) or end_line > len(lines):
        raise ValueError(
            f"span {start_line}-{end_line} is out of range "
            f"({len(lines)} lines); file not written"
        )
    addition = _to_lf(new_content)
    if addition == "":
        new_lines = []
    elif addition.endswith("\n"):
        new_lines = _split_lines(addition)
    else:
        new_lines = [addition]
    updated_lines = lines[: start_line - 1] + new_lines + lines[end_line:]
    updated = "\n".join(updated_lines)
    if text.endswith("\n") or (text == "" and updated_lines):
        updated += "\n"
    if updated == text:
        raise ValueError("replace_range did not change the file; file not written")
    payload = _from_lf(updated, newline).encode("utf-8")
    if b"\r\r\n" in payload:
        raise ValueError("refusing to write doubled CR; file not written")
    target.write_bytes(payload)
    shown = raw.lstrip("./") or raw
    summary = "\n".join([
        f"path: {shown}",
        f"start_line: {start_line}",
        f"end_line: {end_line}",
        f"replaced_lines: {end_line - start_line + 1}",
        f"new_lines: {len(new_lines)}",
        f"size_bytes: {len(payload)}",
        f"newline: {newline!r}",
    ])
    log_event(
        "file_replace_range",
        f"Replaced lines in {raw}",
        detail={
            "path": raw,
            "start_line": start_line,
            "end_line": end_line,
            "replaced_lines": end_line - start_line + 1,
            "new_lines": len(new_lines),
            "bytes": len(payload),
        },
    )
    append_context(
        f"- File change: replaced lines {start_line}-{end_line} in `{raw}`"
    )
    return summary


def apply_patches(patches) -> str:
    """Apply a list of patches in order. Stops on the first error.

    Each item is {path, search, replace} or {path, start_line, end_line, new_content}.
    Does not replace write_file, patch_file, or replace_range.
    Does not update cos/tasks.md. Not copy/move. Not create_files.
    A failed later patch does not roll back earlier successes.
    The summary names what succeeded and the error that stopped the rest.
    """
    if isinstance(patches, str):
        stripped = patches.strip()
        if stripped.startswith("["):
            try:
                patches = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"patches is not a JSON list: {exc}") from exc
    if not isinstance(patches, list) or not patches:
        raise ValueError("patches must be a non-empty list")
    done = []
    for index, item in enumerate(patches, start=1):
        if not isinstance(item, dict):
            summary = _apply_patches_stop(done, index, "patch is not an object")
            raise ValueError(summary)
        try:
            kind, detail = _apply_one_patch(item)
        except (ValueError, FileNotFoundError, OSError) as exc:
            summary = _apply_patches_stop(done, index, str(exc))
            raise ValueError(summary) from exc
        done.append(f"{index}: {kind} {detail}")
    summary = "\n".join([
        f"applied: {len(done)}",
        "stopped: no",
    ] + done)
    log_event(
        "file_apply_patches",
        f"Applied {len(done)} patch(es)",
        detail={"applied": len(done), "stopped": False},
    )
    append_context(f"- File change: apply_patches applied {len(done)}")
    return summary


def _apply_patches_stop(done, index, error) -> str:
    """Build the stop summary. Does not write. Does not roll back."""
    lines = [
        f"applied: {len(done)}",
        "stopped: yes",
        f"stopped_at: {index}",
        f"error: {error}",
    ]
    lines.extend(done)
    log_event(
        "file_apply_patches",
        f"Stopped apply_patches at {index}",
        detail={"applied": len(done), "stopped": True, "stopped_at": index},
    )
    if done:
        append_context(
            f"- File change: apply_patches applied {len(done)}, stopped at {index}"
        )
    return "\n".join(lines)


def _apply_one_patch(item: dict):
    """Apply one patch item by calling the existing single-file tools."""
    path = item.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path is required")
    has_search = "search" in item
    has_span = "start_line" in item or "end_line" in item
    if has_search and has_span:
        raise ValueError("patch mixes search and line span; file not written")
    if has_search:
        replace = item.get("replace", "")
        if not isinstance(replace, str):
            raise ValueError("replace must be a string")
        replace_all = item.get("replace_all", False)
        summary = patch_file(path, item.get("search"), replace, replace_all)
        return "patch_file", path.strip().replace("\\", "/")
    if has_span:
        if "start_line" not in item or "end_line" not in item:
            raise ValueError("line span needs start_line and end_line")
        new_content = item.get("new_content", "")
        if not isinstance(new_content, str):
            raise ValueError("new_content must be a string")
        summary = replace_range(
            path, item.get("start_line"), item.get("end_line"), new_content
        )
        return "replace_range", path.strip().replace("\\", "/")
    raise ValueError("patch needs search or a line span")


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


def _check_output_text(raw: str, label: str) -> str:
    """Cap one captured stream. Say when truncated. Does not change the file."""
    if raw is None:
        raw = ""
    if not isinstance(raw, str):
        raw = str(raw)
    raw = raw.replace("\r\n", "\n")
    limit = RUN_CHECK_OUTPUT_CHAR_LIMIT
    if len(raw) <= limit:
        return raw
    omitted = len(raw) - limit
    return (
        raw[:limit]
        + f"\nTRUNCATED {label}: omitted {omitted} characters\n"
    )


def _normalize_check_args(args) -> list:
    """Args that are not flags. Empty is allowed. Never returns a dash arg."""
    if args is None:
        return []
    if isinstance(args, str):
        raise ValueError("args must be a list of strings, not a string")
    if not isinstance(args, (list, tuple)):
        raise ValueError("args must be a list of strings")
    if len(args) > RUN_CHECK_MAX_ARGS:
        raise ValueError(f"too many args ({len(args)}); max {RUN_CHECK_MAX_ARGS}")
    out = []
    for item in args:
        if not isinstance(item, str):
            raise ValueError("each arg must be a string")
        if item == "" or item.strip() != item:
            raise ValueError("args must be non-empty and not padded with space")
        if len(item) > RUN_CHECK_ARG_MAX:
            raise ValueError(f"arg longer than {RUN_CHECK_ARG_MAX} characters")
        if item.startswith("-") or item.startswith("/") or item.startswith("\\"):
            raise ValueError(f"refusing flag arg: {item}")
        if "\n" in item or "\r" in item or "\x00" in item:
            raise ValueError("refusing arg with a newline or NUL")
        out.append(item)
    return out


def _check_script_path(rel_path: str) -> Path:
    """Resolve one workspace script. Refuse cos/, escapes, and non-files."""
    if not isinstance(rel_path, str) or not rel_path.strip():
        raise ValueError("path is required")
    raw = rel_path.strip().replace("\\", "/")
    if raw.startswith("/") or (len(raw) >= 2 and raw[1] == ":"):
        raise ValueError("absolute path refused")
    if ".." in raw.split("/"):
        raise ValueError("path with .. refused")
    norm = raw.lstrip("./").rstrip("/")
    if not norm:
        raise ValueError("path is required")
    parts = norm.split("/")
    if parts[0] == "cos":
        raise ValueError("refusing a script under cos/")
    if norm in ("cos/cos.py", "cos.py"):
        raise ValueError("refusing to run cos/cos.py")
    target = resolve_safe(norm)
    if target.is_symlink():
        raise ValueError("refusing to follow a symlink")
    if not target.is_file():
        raise FileNotFoundError(norm)
    root = ROOT.resolve()
    resolved = target.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError("Path escapes workspace root")
    rel = resolved.relative_to(root).as_posix()
    if rel == "cos" or rel.startswith("cos/"):
        raise ValueError("refusing a script under cos/")
    return resolved


def run_check(rel_path: str, args=None) -> str:
    """Run python on one workspace script. Not a shell. Not run_command.

    shell=False. No git, no network tool, no flags. Timeout 60 seconds.
    A non-zero exit is returned as text, not raised, so a failing check
    can be read and corrected in the same turn. Timeout and path refusal
    still raise. This function does not write the script and does not commit.
    """
    script = _check_script_path(rel_path)
    arg_list = _normalize_check_args(args)
    py = sys.executable
    if not py or not isinstance(py, str):
        raise RuntimeError("python executable not available")
    root = str(ROOT.resolve())
    argv = [py, str(script)] + arg_list
    for arg in argv[2:]:
        if arg.startswith("-"):
            raise ValueError(f"refusing unexpected flag: {arg}")
    shown = script.relative_to(ROOT.resolve()).as_posix()
    log_event(
        "check_call",
        f"run_check {shown}",
        refs=[shown],
        detail={"path": shown, "args": arg_list, "timeout": RUN_CHECK_TIMEOUT_SEC},
    )
    try:
        result = subprocess.run(
            argv,
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=RUN_CHECK_TIMEOUT_SEC,
            shell=False,
        )
    except subprocess.TimeoutExpired as e:
        log_event(
            "check_result",
            f"run_check {shown} timed out",
            detail={"path": shown, "timeout": RUN_CHECK_TIMEOUT_SEC},
        )
        raise RuntimeError(
            f"run_check timed out after {RUN_CHECK_TIMEOUT_SEC}s: {shown}"
        ) from e
    except OSError as e:
        raise RuntimeError(f"run_check failed to start: {e}") from e
    stdout = _check_output_text(result.stdout or "", "stdout")
    stderr = _check_output_text(result.stderr or "", "stderr")
    text = "\n".join([
        f"path: {shown}",
        f"exit: {result.returncode}",
        "stdout:",
        stdout if stdout else "(empty)",
        "stderr:",
        stderr if stderr else "(empty)",
    ])
    log_event(
        "check_result",
        f"run_check {shown} exit {result.returncode}",
        detail={
            "path": shown,
            "exit": result.returncode,
            "output_preview": _clip(text, 400),
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
    At most one commit and one push per tool-loop turn. A successful push sets
    stop_after_push so the caller ends the turn instead of chasing a clean tree.
    """
    action = (action or "").strip().lower()
    if action not in GIT_ACTIONS:
        raise ValueError(
            "action must be one of: " + ", ".join(GIT_ACTIONS)
        )
    if action == "commit" and _TURN_GIT.get("commits", 0) >= 1:
        raise ValueError(
            "commit refused: one commit per turn already used; "
            "do not chase log dirtiness in the same turn"
        )
    if action == "push" and _TURN_GIT.get("pushes", 0) >= 1:
        raise ValueError(
            "push refused: one push per turn already used; "
            "residual log dirtiness belongs to the next directive"
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
    if action == "commit":
        _TURN_GIT["commits"] = _TURN_GIT.get("commits", 0) + 1
    if action == "push":
        _TURN_GIT["pushes"] = _TURN_GIT.get("pushes", 0) + 1
        _TURN_GIT["stop_after_push"] = True
        text += (
            "\n[turn stop] push succeeded. "
            "Do not add, commit, status, or push again this turn. "
            "Logging rewrites cos/context.md and cos/event_log.jsonl, "
            "so a clean tree after push is impossible in the same turn.\n"
        )
    return text
