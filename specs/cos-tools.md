# COS Tools Specification v1.3
## File, Context, Execution, Internet, and Agentic Tools

**Status:** v1.3 signed off; not authorized for implementation
**Version:** 1.3
**v1.2 corrected:** 2026-10-22, by the explicit directive "do step 1"
**v1.2 sign-off:** 2026-10-22. The user signed off that v1.2 matched the review. That sign-off does not cover v1.3 and does not start step 2.
**v1.3 updated:** 2026-10-22, by the explicit directive "Update the spec files and plan"
**v1.3 sign-off:** 2026-10-22, by the explicit directive "I approve". Covers the no-loss amendment in §2.3 and the matching plan. Does not start step 2. Does not authorize editing `cos/cos.py`.
**Owner:** Local COS
**Parent:** specs/META-COS.md
**Plan:** specs/cos-tools-plan.md (ordered slices; booking is not approval)
**Related:** specs/claude-api-proposal.md (proposal only; client not built)
**Policy:** cos/prompts/ask-first.md (locked; this spec does not weaken it)
**Workspace root:** The directory containing this specs/ folder (OneDrive-synced, Git repo)

This document is the tool catalog and the contract for a shared tool surface. It is not a work order. "Corrected" means the catalog matches the live process and the agreed gates. It does not mean any slice may be implemented.

Implementation of any slice requires an explicit user instruction that names that slice. A prior slice's sign-off does not start the next one. Ask-first still holds. Reading this file is not permission to edit `cos/cos.py`.

v1.3 does not reopen step 1 and does not start step 2. It locks one rule the user stated after the v1.2 sign-off: existing enabled tools come first, and a later slice must not lose current functionality. Signed off 2026-10-22 by "I approve". That sign-off does not start step 2.

## 1. Purpose

Give the COS a complete, efficient, and safe toolkit so it can inspect, edit, and search the workspace while minimizing token use and round-trips. Prefer partial and single-file operations over full-file read/write when a later, signed-off slice has made those tools live.

The surface is shared. Grok uses the live tools now. A Claude API client is assumed later, from `specs/claude-api-proposal.md`. This spec does not build that client, does not load a key, and does not call Claude.

Delivery is ordered. One slice, then test, verify, and user sign-off. Not all of section 3 in one change to `cos/cos.py`. The order is `specs/cos-tools-plan.md` steps 1–6 for the first wave. Later slices stay unstarted until their own instruction.

Existing tools are the priority. New tools are added beside them. They do not replace them.

## 2. Global Rules

Rules that already apply to live tools:

- Every path argument is resolved relative to the workspace root.
- Any path that resolves outside the workspace root is rejected with an error.
- Live tools log a tool event (name, arguments summary, result preview). New tools must do the same once implemented.
- No general shell. No unattended commit or push. No key handling. No paths outside the workspace.
- This spec does not weaken ask-first, the git approval phrases, or META-COS §2.
- `cos/tasks.md` is updated only via `update_task`. Do not rewrite it with `write_file` or a future `patch_file`.
- §2.3 (no loss) overrides any later slice that would drop, rename, or weaken a live tool.

Rules that apply when a slice is later implemented:

- Edit and write tools return a concise human-readable summary (for example "patched +4 −1 lines" or a short diff).
- Maximum tool-result size returned to the model is capped at 12,000 characters. The cap is a requirement. It is not enforced in `cos/cos.py` until step 2 of the plan is explicitly instructed and signed off. Today the process cap is 120,000 characters (`TOOL_RESULT_CHAR_LIMIT`). See §2.3 for how the smaller cap may be applied without removing `read_file`.
- Network size caps and network logging apply only if a §3.7 tool is later approved. They are not live.

### 2.1 Live tools (this process)

These tools exist in the Grok tool loop today. They are registered in `cos/cos.py` and named in the process system prompt. They are the priority set. A slice that does not leave all six working has failed, even if the new tools work.

| Tool | Role |
|------|------|
| `list_dir(path)` | List a workspace-relative directory. `.` is the root. Does not follow symlinks. Does not expand `.git`, `__pycache__`, `.venv`, `node_modules`, or `.idea`. Depth and entry count are capped. |
| `read_file(path)` | Read a UTF-8 text file by workspace-relative path. Full file. Stays available after partial reads exist. |
| `write_file(path, content)` | Create or replace a UTF-8 text file with the complete raw body. Parents created if needed. Cannot overwrite `cos/tasks.md`. |
| `update_task(id, status, title, note)` | Add or update one task in `cos/tasks.md`. Statuses: `open`, `doing`, `blocked`, `done`. At most one `doing`. |
| `git(action, paths, message, remote, branch)` | Bounded git, not a shell. `status`, `diff`, and `log` need no approval. `add` stages explicit workspace paths only. `commit` requires the exact phrase `approve git commit` in the current directive. `push` requires the exact phrase `approve git push` in the current directive. At most one commit and one push per turn. A successful push ends the turn. No force, reset, clean, config, or `commit --amend`. |
| `handoff(task)` | Emit a self-contained Claude prompt for the user to paste into VS Code. Does not call Claude. |

`run_command` is not a live tool. It is a §3.6 proposal, on hold.

Manual user commands (`read:`, `write:`, `improve:`, `handoff:`, `tasks`, `git`) are a separate interface. They are not additional agent tools. They stay.

Also already live, and not to be dropped by a tools slice: the 10-round tool loop, the one no-tools final answer when the cap is hit, push-ends-the-turn, deep context capped at 1,000,000 bytes, the event log, and console wrap. Those are process behavior, not catalog tools. §2.3 covers them anyway.

### 2.2 Approval gate

- Status of this spec is not "Ready for implementation".
- §5 and §7 do not authorize the COS to implement this file by editing `cos/cos.py`.
- An explicit instruction that names the slice is required before that slice is started. Sign-off of a finished slice is a separate explicit instruction.
- The system-prompt paragraph in §4 is not loaded and must not be added until the tools it names actually exist, and only under its own explicit instruction.
- Updating this file to v1.3 does not name step 2. Step 2 still needs its own instruction.
- v1.3 was signed off 2026-10-22 by "I approve". That sign-off does not start step 2.

### 2.3 Existing tools first (no loss)

Locked 2026-10-22 by "Update the spec files and plan", after the user stated that existing enabled tools must be prioritized and that current functionality must not be lost. Signed off the same day by "I approve".

This section wins over a slice description that would remove or weaken a live tool.

- The six tools in §2.1 stay registered, under those names, with those gates. A later slice may add tools beside them. It may not remove, rename, unregister, or replace them.
- Ask-first stays. Git approval phrases stay. One commit and one push per turn stay. A successful push still ends the turn. `write_file` still cannot overwrite `cos/tasks.md`. Paths still cannot escape the workspace. There is still no general shell.
- Full-file `read_file` and `write_file` stay. Partial reads and `patch_file` are additions, not substitutes.
- The tool loop, deep-context load, event log, manual commands, and console wrap stay.
- A failure in a new tool must not take down `list_dir`, `read_file`, `write_file`, `update_task`, `git`, or `handoff`.
- Step 2, if later instructed, adds only the 12,000-character result cap, `file_info`, and `file_outline`. No search, no patch, no shell, no Claude client in that step.
- The 12,000-character cap is a result-size policy, not removal of `read_file`. The file on disk is unchanged. When the cap is applied, the returned text must say it was truncated and how much was omitted. The model must not be left to treat a truncated read as the whole file. Until partial reads exist (step 3), that notice is the recovery path. After step 3, the notice should point at the partial-read tools.
- Lowering the process cap from 120,000 to 12,000 is allowed only inside an instructed step 2, and only with the truncation notice above. It is not a silent change, and it is not a reason to drop full-file `read_file`.
- This section does not start step 2. It does not edit `cos/cos.py`.

## 3. Tool Catalog

Section 3 is the target catalog. Except for the live tools in §2.1, nothing here is implemented. First-wave slices are steps 2–5 of `specs/cos-tools-plan.md`. Anything not in that wave stays unstarted after step 6.

Every first-wave slice is additive. It must still satisfy §2.3.

### 3.1 Information and discovery

**First wave (plan step 2):** `file_info`, `file_outline`, plus the 12,000-character tool-result cap. Additive only. The six live tools stay.

**First wave (plan step 4):** `list_files`, `search_files`.

- **file_info(path)**
  Returns: size_bytes, line_count, mtime_iso, sha256.

- **file_outline(path)**
  Returns a structural summary:
  - Code: class and function names with line numbers
  - Markdown: headings
  - JSON: top-level keys
  - Other: first/last few lines indicator

- **list_files(path=".", glob=None, recursive=False)**
  Returns a list of relative paths. `glob` supports simple patterns (`*.kt`, `*.json`). Recursion is off unless asked. `.git` is not expanded. Does not replace `list_dir`.

- **search_files(pattern, path=".", context_lines=2)**
  In-workspace search. Returns a list of {path, line_number, line, context}. Results obey the 12,000-character cap.

### 3.2 Partial reads

**First wave (plan step 3):** `read_file_range`, `read_file_head`, `read_file_tail`, `search_in_file`.

**Not in the first wave:** `read_files`.

- **read_file_range(path, start_line, end_line)**
  Inclusive 1-based line range. Returns content plus "lines X–Y of Z". An out-of-range request errors instead of reading the whole file.

- **read_file_head(path, n=40)**
- **read_file_tail(path, n=40)**

- **search_in_file(path, pattern, context_lines=2)**
  Returns matching lines with context inside a single file.

- **read_files(paths)**
  Not in the first wave. `paths` is a list of strings. Returns a dict of path → content (or error string). Results obey the cap.

Full-file `read_file` stays available after partial reads exist. Partial reads do not replace it.

### 3.3 Partial writes and edits

**First wave (plan step 5):** `patch_file` only. Unique literal match. Short diff. No multi-file writes in that step.

**Not in the first wave:** `append_file`, `insert_at_line`, `replace_range`, `apply_patches`.

- **append_file(path, content)**
  Creates the file if missing. Returns a summary.

- **insert_at_line(path, line_number, content)**
  Inserts before the given 1-based line. Returns a summary.

- **replace_range(path, start_line, end_line, new_content)**
  Replaces the inclusive line span. Returns a summary.

- **patch_file(path, search, replace, replace_all=False)**
  Literal search/replace.
  - If `replace_all` is false and the search occurs any number other than 1, return an error and do not write.
  - A missing search does not write.
  - Returns a short diff summary.
  - Does not update `cos/tasks.md`. That file stays on `update_task` only.
  - Does not replace `write_file`. Full-file replace stays available.

- **apply_patches(patches)**
  Not in the first wave. `patches` is a list of `{path, search, replace}` or `{path, start_line, end_line, new_content}`. Applied sequentially. Returns a combined summary. Stops on first error and reports what succeeded.

### 3.4 Multi-file utilities

**Not in the first wave.** None of these are started by steps 1–6.

- **copy_file(src, dst)**
- **move_file(src, dst)**
- **create_files(files)**
  `files` is a dict of relative_path → full_content. Creates parent directories as needed.
- **file_set_hash(paths)**
  Returns a dict of path → sha256.

### 3.5 Context management

**Not in the first wave.** None of these are started by steps 1–6.

- **context_stats()**
  Returns: context_bytes, truncated (bool), event_count, context_file_bytes, spec_bytes.

- **summarize_context(max_bytes=300000)**
  Compresses older material in `cos/context.md` so the file is at or under max_bytes, preserving recent detail. Logs before/after sizes. Returns a reduction summary.

### 3.6 Execution tools

**Status: on hold.** Not deleted. Not in the first wave. Steps 1–6 do not implement this section. A later slice still needs its own explicit instruction. On hold is not approval.

These tools are not a general shell. Even if a later instruction approves a bounded executor, it must not weaken META-COS §2 (no unattended commit or push, no replacing Git, Android Studio, or PlatformIO) and must not become an open `shell` tool. `run_command` is not live. Adding any of these later must still leave the six live tools working (§2.3).

- **run_command(cmd, cwd=None, timeout=90)**
  Proposed, on hold. Would run via the system shell and return {stdout, stderr, exit_code, timed_out}. Output truncated if excessive. `cwd` must be inside the workspace or omitted. Not authorized by this spec.

- **run_command_async(cmd, cwd=None)**
  Proposed, on hold. Would start a command in the background and return a handle.

- **command_status(handle)**
  Proposed, on hold. Would return {running, stdout_so_far, stderr_so_far, exit_code or null}.

- **command_kill(handle)**
  Proposed, on hold. Would terminate the process and return confirmation.

- **which(program)**
  Proposed, on hold. Would return the full path if found, otherwise "not found". Would use `where.exe` on Windows.

- **winget_install(id)**
  Proposed, on hold. Would run `winget install --id {id} -e --accept-source-agreements --accept-package-agreements`.

- **pip_install(packages)**
  Proposed, on hold. `packages` would be a list of strings.

- **download_file(url, dest_path)**
  Proposed, on hold. Would download to a workspace-relative path. Also listed here because it writes local bytes from the network. Not a §3.7 read.

- **run_script(path, args=None)**
  Proposed, on hold. Would resolve `path` inside the workspace and dispatch by extension (`.py`, `.ps1`, `.bat`/`.cmd`, or direct). `args` would be an optional list.

### 3.7 Internet access

**Status: on hold.** Not deleted. Not in the first wave. Steps 1–6 do not implement this section. A later slice still needs its own explicit instruction. On hold is not approval.

No external POST/PUT that mutates external state. No key handling. If ever approved, responses are size-capped and the URL plus a result summary are logged. Adding any of these later must still leave the six live tools working (§2.3).

- **web_search(query, num_results=8)**
  Proposed, on hold. Would return a list of {title, url, snippet}.

- **browse_page(url, instructions=None)**
  Proposed, on hold. Would fetch the page, extract main text, and (if instructions were given) summarize or extract according to them. Cleaned content capped at about 12,000 characters.

- **http_get(url)**
  Proposed, on hold. Would return {status_code, content_type, body_preview}. Body capped.

## 4. System Prompt Update

Do not add a toolkit paragraph to the system prompt as part of correcting this spec, and do not add it when a slice is implemented unless a separate explicit instruction says to update the prompt.

The v1.1 paragraph is withdrawn. It named tools that are not live, including on-hold execution and internet tools, and it read as a standing order to use them. Loading it would violate ask-first and this spec's approval gate.

A replacement paragraph may be proposed only after the tools it names exist in `cos/cos.py`, and it may be loaded only under an explicit instruction. Until then the live tool list in the COS process prompt remains the source of truth.

v1.3 does not change the process prompt. The six live tools stay the ones the prompt names. New names are added to that prompt only when those tools exist, and only under an explicit instruction. Do not advertise `file_info` or `file_outline` before step 2 is implemented.

## 5. Acceptance Criteria

Acceptance is per slice, not for the whole catalog at once. The COS must not treat this section as permission to start work.

**v1.2 (plan step 1) was accepted when:**

- The file on disk matched the corrections in the plan's step 1.
- No `cos/cos.py` change was made in that step.
- The user signed off on 2026-10-22.

**v1.3 (no-loss amendment) was accepted when:**

- This file was on disk at v1.3 and §2.3 stated the no-loss rule.
- `specs/cos-tools-plan.md` matched this version and did not still say the parent spec is v1.1 or that step 1 is open.
- No `cos/cos.py` change was made in that update.
- The user signed off on 2026-10-22 by "I approve".

That sign-off does not start step 2.

**A later slice is accepted only when:**

- An explicit instruction named that slice before it was started.
- The slice's verify checks in `specs/cos-tools-plan.md` passed, including the no-loss checks.
- The user signed off that slice.
- The six live tools are still registered under the same names and gates.
- Full-file `read_file` and `write_file` still work. `write_file` still cannot overwrite `cos/tasks.md`.
- Path escape attempts are rejected.
- Tool results returned to the model stay within 12,000 characters once the cap exists, and a truncated result says it was truncated.
- Ask-first, the git approval phrases, and META-COS §2 still hold.
- Existing deep-context loading, event logging, manual commands, console wrap, and the multi-round tool loop still work.
- `cos/tasks.md` is still updated only via `update_task`.
- A forced failure in a new tool does not remove or break the six live tools.

**Not acceptance criteria for the first wave:**

- Every tool in section 3 is registered.
- `read_files` and `apply_patches` update two files in one round.
- `context_stats` or `summarize_context` runs.
- `which`, `run_script`, `pip_install`, `web_search`, `browse_page`, or `download_file` runs.
- The COS reads this spec and implements it by editing `cos/cos.py`.
- Any live tool was removed to make room for a new one.

Those older v1.1 criteria apply only to a slice that has its own explicit instruction after the first-wave gate. They are not a backlog.

## 6. Non-Goals

- Binary or image editing
- Privilege elevation
- Automatic Git commit/push (remains a separate explicit policy; live `git` still requires the approval phrases)
- Arbitrary POST/PUT that mutates external state
- Network servers or long-lived daemons
- A general shell, including treating §3.6 as one
- Replacement of the existing agentic loop architecture
- Removing, renaming, or weakening `list_dir`, `read_file`, `write_file`, `update_task`, `git`, or `handoff`
- Replacing full-file `read_file` or `write_file` with partial tools
- Building the Claude API client
- Implementing §3.6 or §3.7 in the first wave
- Silent adoption of this catalog
- Treating v1.3 as permission to start step 2

## 7. Implementation Note

This spec is not a self-implement order. The v1.1 sentence that told the COS to read this file and edit `cos/cos.py` is withdrawn.

Implementation happens only when the current user directive explicitly names the slice. The order and the verify checks live in `specs/cos-tools-plan.md`. After an instructed slice, record the result in the event log. Do not start the next slice in the same turn. Do not run an internet call as a self-test unless that §3.7 tool has been explicitly authorized.

Step 6 of the plan is a stop. Finishing the first wave does not authorize remaining §3.3 and §3.4 writes, §3.5 context tools, §3.6, §3.7, the Claude client, or the §4 prompt paragraph.

Any instructed slice must satisfy §2.3 before it can be called done. If a change would drop current functionality, do not make it. Report the conflict and wait.

## 8. Change

Do not silently relax, reinterpret, or drop the approval gate, the on-hold marks, the live-tool list, or §2.3. A change requires an explicit user instruction, a logged acceptance, and an update to this file.

v1.2 remains the signed-off correction of the catalog. v1.3 is the no-loss amendment written under "Update the spec files and plan" and signed off 2026-10-22 by "I approve". That sign-off does not start step 2.

---

*— End of Specification —*
