# COS Tools — Initial Implementation Plan

**Status:** steps 1–6 done and signed off. First wave closed. Next slice not started.
**Booked:** 2026-10-22, by the explicit directive "Book this initial plan"
**Step 1 done:** 2026-10-22, by "do step 1". User signed off the same day: v1.2 matched the review.
**Plan updated:** 2026-10-22, by the explicit directive "Update the spec files and plan"
**v1.3 sign-off:** 2026-10-22, by the explicit directive "I approve". Does not start step 2.
**Step 2 done:** 2026-10-22, by "do step 2". User signed off the same day: "I sign off step 2". Script passed (`fails 0`). Does not start step 3.
**Step 3 done:** 2026-10-23, by "do step 3". User signed off the same day: "sign off". Script passed (`fails 0`). Does not start step 4.
**Step 4 done:** 2026-10-23, by "do step 4". User signed off the same day: "sign-off". Script passed (`fails 0`). Does not start step 5.
**Step 5 done:** 2026-10-23, by "do step 5". User signed off the same day: "sign-off". Script passed (`fails 0`). Does not start step 6.
**Step 6 done:** 2026-10-23, by "do step 6". User signed off the same day: "sign off". First wave closed at steps 1–5. Does not start a later slice.
**Parent spec:** specs/cos-tools.md v1.3 (v1.2 and v1.3 signed off; v1.3 sign-off does not start step 2)
**Related:** specs/claude-api-proposal.md (proposal only; client not built)
**Does not authorize:** editing cos/cos.py for a new slice, starting a later slice, calling Claude, restoring the tool-result cap, or git commit/push

This file records the ordered plan so progress can be made one step at a time. Booking it was not approval to start a step. Updating it is not approval to start a step. Signing off a finished step does not start the next one. Each step needs its own explicit instruction that names that step. Ask-first still holds.

This update records the step 6 sign-off. It does not start a later slice. It does not restore the tool-result cap.

## Rules for every step

- One step per explicit instruction. Do not start the next step in the same turn.
- Test, verify, and sign off before the next step is eligible.
- Sign-off is an explicit user instruction, not a COS self-check.
- Do not implement section 3.6 or 3.7 in steps 1–6. Those sections stay **on hold**.
- Do not build the Claude API client in this plan. The tool surface is shared so that client can use it later.
- Do not weaken ask-first, the git approval phrases, or META-COS §2.
- No general shell, no unattended commit or push, no key handling, no paths outside the workspace.
- **Existing tools first.** `list_dir`, `read_file`, `write_file`, `update_task`, bounded `git`, and `handoff` stay registered under those names and gates. A slice may add tools beside them. It may not remove, rename, unregister, or replace them. Full-file `read_file` and `write_file` stay. The tool loop, deep context, event log, manual commands, and console wrap stay. A failure in a new tool must not take the six down. Detail is `specs/cos-tools.md` §2.3.
- **Cap stays off.** `TOOL_RESULT_CHAR_LIMIT` is `None` until the partial IO tools are in place and the user has verified that. Step 5 sign-off is not that restore. Do not turn the 12,000-character cap back on in a later slice unless a later directive says to.
- Not committed by this update. Commit still needs `approve git commit` in a later directive.

## Step 1 — Correct the spec

**Task id:** `tools-1-spec`
**Status:** done. Signed off 2026-10-22. This update does not reopen it.

Made `specs/cos-tools.md` current at v1.2. Document only. No tool code.

Corrections that were signed off:

- Live tools are `list_dir`, `read_file`, `write_file`, `update_task`, bounded `git`, and `handoff`. `run_command` is not live.
- Status is no longer "Ready for implementation". Implementation of any slice requires an explicit instruction that names that slice.
- §5 and §7 self-implement lines are conditional on that same approval. They are not a work order.
- §3.6 Execution and §3.7 Internet stay in the catalog, marked **on hold**. Not deleted. Not in the first wave.
- Shared surface: Grok uses the tools now. A Claude API client is assumed later, from `specs/claude-api-proposal.md`. This spec does not build that client.
- Delivery is ordered: one slice, then test, verify, and sign off. Not all of section 3 in one change to `cos/cos.py`.
- Tool-result cap of 12,000 characters stays a requirement. It is not enforced until step 2.
- Non-goals stay: no binary edits, no privilege elevation, no automatic git, no external POST/PUT, no daemons, do not replace the tool loop.

**Done when:** met. Corrected spec was on disk, this step was marked done, and the user signed off. No `cos/cos.py` change in that step.

**Follow-on, not a reopen:** v1.3 adds §2.3 (no loss) under "Update the spec files and plan". Signed off 2026-10-22 by "I approve". That sign-off does not start step 2.

## Step 2 — Result cap and file info

**Task id:** `tools-2-info`
**Status:** done. Signed off 2026-10-22 by "I sign off step 2". This update does not reopen it and does not start step 3.

Added the 12,000-character tool-result cap, `file_info`, and `file_outline` in `cos/cos.py`. Registered beside the six live tools, not instead of them. Paths stay inside the workspace.

**No loss (held):**

- The six live tools stay, same names, same gates.
- Full-file `read_file` and `write_file` still work. `write_file` still cannot overwrite `cos/tasks.md`.
- Ask-first, git approval phrases, one commit and one push per turn, and push-ends-the-turn stay.
- Tool loop, deep context, event log, manual commands, and console wrap stay.
- No search, no patch, no shell, no Claude client in this step.

**Cap, as signed off:** `TOOL_RESULT_CHAR_LIMIT` was 12,000. A truncated result said `TRUNCATED` and how many characters were omitted. The file on disk was unchanged. Full-file `read_file` stayed.

**Later, not a reopen of step 2:** the user ordered the cap off so step 3 could be written, then ordered it to stay off until partial IO is in place and verified with them. Step 4 sign-off does not restore it. Current value is `None`.

## Step 3 — Partial reads

**Task id:** `tools-3-read`
**Status:** done. Signed off 2026-10-23 by "sign off". This update does not reopen it and does not start step 4.

Added `read_file_range`, `read_file_head`, `read_file_tail`, and `search_in_file` beside the existing tools. Inclusive 1-based ranges. A range read returns `lines X-Y of Z`. An out-of-range, inverted, or over-400-line request errors and does not return the whole file. Head and tail default to 40 lines, max 400. `search_in_file` is a literal search in one file. It is not `search_files`.

**No loss (held):**

- The six live tools stay, same names, same gates.
- Full-file `read_file` and `write_file` still work. `file_info` and `file_outline` stay.
- `search_files` is not registered in this step. No patch, no shell, no Claude client in this step.
- `TOOL_RESULT_CHAR_LIMIT` stayed `None`. The 12,000 cap was not restored.

**Verify:** met. `python protocol/step3_self_test.py` printed eighteen `PASS` lines and `fails 0`, recorded in `cos/TestResults.txt`. That covered the tool list, cap off, no `search_files`, range header and body, head, tail, out-of-range, inverted range, path escape, search hit and miss, and full `read_file`. The user signed off the same day.

**Done when:** met. Those checks passed and the user signed off. Step 4 was not started in that turn.

**Not part of the product. Do not stage:** `protocol/step3_self_test.py`, `protocol/extract_cos_sections.py`, `protocol/assemble_cos_step3.py`, `protocol/rebuild_cos_step3.py`, `protocol/uncap_tool_results.py`, `protocol/_fix_search_ctx.py`, `protocol/_step3_new.py`, `protocol/_step3_schemas.py`, `protocol/_step3_exec.py`, and `protocol/_cos_parts/`.

## Step 4 — Search across files

**Task id:** `tools-4-search`
**Status:** done. Signed off 2026-10-23 by "sign-off". This update does not reopen it and does not start step 5. Step 5 was started later by its own instruction.

Added `search_files` and `list_files` (simple glob, recursion off unless asked). In-workspace only. These do not replace `list_dir`. `.git` stays unexpanded.

These are additions. The six live tools stay. `file_info`, `file_outline`, and the four partial reads stay. No patch, no shell, no Claude client in this step. The 12,000-character cap was not restored.

**No loss (held):**

- The six live tools stay, same names, same gates.
- `search_in_file` still searches one file only. `list_dir` still works.
- `patch_file` is not registered.
- `TOOL_RESULT_CHAR_LIMIT` stayed `None`.

**Verify:** met. `python protocol/step4_self_test.py` printed eleven `PASS` lines and `fails 0`, recorded in `cos/TestResults.txt` (19:18:50Z). That covered the tool list through `search_files` and `list_files`, cap off, no `patch_file`, a known search hit and a miss, `*.json` under `protocol/` staying in the workspace, `.git` not expanded, `list_dir`, `search_in_file` still one file, and path escape. The user signed off the same day.

**Done when:** met. Those checks passed and the user signed off. Step 5 was not started in that turn.

**Not part of the product. Do not stage:** `protocol/step4_self_test.py` and `protocol/_step4_insert.py`.

## Step 5 — One-file patch

**Task id:** `tools-5-patch`
**Status:** done. Signed off 2026-10-23 by "sign-off". This update does not reopen it. Step 6 was recorded later by "do step 6".
**Was blocked on:** step 4 sign-off. That block was lifted by the step 4 sign-off. The step was started later by "do step 5".

Add `patch_file` with literal search/replace. If `replace_all` is false and the search occurs any number other than 1, return an error and do not write. Return a short diff summary. Do not add `apply_patches`, `replace_range`, or multi-file writes in this step. Do not replace `write_file`.

**Verify:** a unique literal patch on a scratch file succeeds and the diff matches the change; a non-unique search does not write; a missing search does not write; `write_file` still replaces a file; `cos/tasks.md` remains updatable only via `update_task`.

**Done when:** met. Those checks passed and the user signed off. Step 6 was not started in that turn.

**Not part of the product. Do not stage:** `protocol/step5_self_test.py` and `protocol/_step5_insert.py`.

## Step 6 — Sign-off gate for anything further

**Task id:** `tools-6-gate`
**Status:** done. Signed off 2026-10-23 by "sign off". This sign-off does not start a later slice.
**Was blocked on:** step 5 sign-off. That block was lifted by the step 5 sign-off. The step was started later by "do step 6". No new tool in this step.

No new tool. Steps 1–5 are the first wave. The next slice is a separate decision and needs its own explicit instruction.

Still not started, and not implied by recording this gate:

- Remaining §3.3 and §3.4 writes (`append_file`, `insert_at_line`, `replace_range`, `apply_patches`, copy/move, `create_files`).
- §3.5 context tools (`context_stats`, `summarize_context`).
- §3.6 execution tools. **On hold.** Includes `run_command`. Not a general shell.
- §3.7 internet tools. **On hold.**
- Claude API client. Separate proposal. Not built by this plan.
- Restoring the 12,000-character tool-result cap. Separate instruction, and only after the user verifies partial IO.
- System-prompt paragraph in `specs/cos-tools.md` §4. Add it only when the tools it names actually exist, and only under an explicit instruction.

**Done when:** this gate is written into the task list as the end of the first wave, and you sign off. The next slice still needs its own instruction. The six live tools are still present. `file_info`, `file_outline`, the four partial reads, `search_files`, `list_files`, and `patch_file` stay.

**Signed off:** 2026-10-23, by "sign off". The gate is in this file and in `cos/tasks.md`. This sign-off does not start a later slice.

## What the v1.3 update did not do

- Did not edit `cos/cos.py`.
- Did not start step 2, and did not mark `tools-2-info` doing.
- Did not implement `file_info`, `file_outline`, or the 12,000-character cap.
- Did not itself sign off v1.3. The user signed off later the same day by "I approve". That sign-off does not start step 2.
- Did not commit or push.
- Did not edit `specs/META-COS.md`, `specs/4runner-aa-obd.md`, or `specs/claude-api-proposal.md`. Those were not the stale documents.
- Did not change `cos/tasks.md` in that update. The sign-off record is a later task note, not a start of step 2.

## What the v1.3 sign-off does not do

- Does not edit `cos/cos.py`.
- Does not start step 2. That step was started later by its own instruction, "do step 2".
- Does not implement any tool.
- Does not commit or push. That still needs `approve git commit` or `approve git push` in a later directive.

## What the step 2 sign-off does not do

- Does not start step 3. That step was started later by its own instruction, "do step 3".
- Does not add partial reads, search, or patch.
- Does not edit `cos/cos.py`.
- Does not commit or push.

## What the step 3 sign-off does not do

- Does not start step 4. That step was started later by its own instruction, "do step 4".
- Does not add `search_files`, `list_files`, or `patch_file`.
- Does not edit `cos/cos.py`.
- Does not restore the 12,000-character tool-result cap. Cap stays `None` until a later directive says otherwise, and only after the user verifies partial IO.
- Does not commit or push. That still needs `approve git commit` or `approve git push` in a later directive.

## What the step 4 sign-off does not do

- Does not start step 5. That step was started later by its own instruction, "do step 5".
- Does not add `patch_file`.
- Does not edit `cos/cos.py`.
- Does not restore the 12,000-character tool-result cap. Cap stays `None` until a later directive says otherwise, and only after the user verifies partial IO.
- Does not commit or push. That still needs `approve git commit` or `approve git push` in a later directive.

## What the step 5 sign-off does not do

- Does not start step 6. That step was started later by its own instruction, "do step 6".
- Does not add `apply_patches`, `replace_range`, or any later-slice tool.
- Does not edit `cos/cos.py`.
- Does not restore the 12,000-character tool-result cap. Cap stays `None` until a later directive says otherwise, and only after the user verifies partial IO.
- Does not commit or push. That still needs `approve git commit` or `approve git push` in a later directive.

## What the step 6 sign-off does not do

- Does not start a later slice by itself. Steps 7 and 8 were booked later by "I want both of these to be injected into are plan as the next items to implement. You choose which order." That booking does not start them.
- Does not edit `cos/cos.py`.
- Does not restore the 12,000-character tool-result cap. Cap stays `None` until a later directive says otherwise, and only after the user verifies partial IO.
- Does not commit or push. That still needs `approve git commit` or `approve git push` in a later directive.

## Step 7 — Raise the tool-round cap

**Task id:** `tools-7-rounds`
**Status:** signed off 2026-10-23 by "sign off". Restart loaded `MAX_TOOL_ROUNDS = 25`. Sign-off does not start step 8.
**Blocked on:** nothing.

Raise `MAX_TOOL_ROUNDS` in `cos/cos.py` from 10 to 25. That is the only code change in this step.

Why this is first: the 10-round cap is what cuts an implementation turn before the write can be checked. A bounded local check is useless if the turn dies before it can run. The early git chase is already fixed (a successful push ends the turn). Do not reopen that. Do not raise the cap to chase a clean tree.

**No loss**
- The six live tools stay, same names and gates. `file_info`, `file_outline`, the four partial reads, `search_files`, `list_files`, and `patch_file` stay.
- Ask-first, git approval phrases, one commit and one push per turn, and push-ends-the-turn stay.
- The 12,000-character tool-result cap stays off. This step does not restore it.
- Hitting the new cap still triggers one no-tools final answer.
- No shell. No `run_command`. No Claude client. No new file tools.

**Done when:** the constant is 25, a restart has loaded it, you have seen a turn that can use more than 10 rounds or a check that the constant is 25, and you sign off. Sign-off does not start step 8.

## Step 8 — Bounded local check

**Task id:** `tools-8-check`
**Status:** signed off 2026-10-23 by "sign-off". `run_check` passed inside the turn (`fails 0`). Sign-off does not start a later slice.
**Blocked on:** nothing.

Add one bounded tool, `run_check`, so a written self-test can be run and corrected inside the same turn. This is not a shell and not `run_command`.

**Scope**
- One command shape: `python` plus one workspace-relative script path, optional args that are not flags.
- The script path must stay inside the workspace. No `..`. No absolute path.
- No shell. `shell=False`. No `cmd`, no PowerShell, no `git`, no network.
- Timeout 60 seconds. Capture stdout and stderr. Cap each at 12,000 characters and say if truncated.
- Refuse if the script path is `cos/cos.py` or anything under `cos/`. Checks live under `protocol/` or another non-`cos` path you name in the instruction.
- Do not stage the check script. Do not commit from the tool.

**No loss**
- Same as step 7. Existing tools and gates stay. Cap stays off.
- A failed check does not write `cos/cos.py` by itself. A later patch in that same turn still needs the current directive to have authorized the step.

**Done when:** `run_check` is registered beside the existing tools, a hand check shows a pass, a fail, a path escape, and a timeout, you sign off, and no general shell exists. Sign-off does not start a later slice.

## What booking steps 7 and 8 does not do

- Does not edit `cos/cos.py`.
- Does not raise `MAX_TOOL_ROUNDS`.
- Does not add `run_check` or `run_command`.
- Does not start step 7. That needs its own instruction, for example "do step 7".
- Does not restore the 12,000-character tool-result cap.
- Does not commit or push.
- Does not start remaining writes, context tools, §3.6, §3.7, or the Claude client.

## What the step 7 write does not do

- Written 2026-10-23 by "do step 7". `MAX_TOOL_ROUNDS` is 25 in `cos/cos.py`. Not live until restart. Awaiting sign-off.
- Does not add `run_check`. Step 8 stays blocked until that sign-off.
- Does not restore the 12,000-character tool-result cap.
- Does not commit or push.

## What the step 7 sign-off does not do

- Signed off 2026-10-23 by "sign off". The restarted process has `MAX_TOOL_ROUNDS = 25`.
- Does not start step 8. `run_check` is not added. That needs its own instruction, for example "do step 8".
- Does not restore the 12,000-character tool-result cap.
- Does not commit or push.
- Does not start remaining writes, context tools, §3.6, §3.7, or the Claude client.

## What the step 8 write does not do

- Written 2026-10-23 by "do step 8". `run_check` is in `cos/cos.py`. Not live until restart. Awaiting sign-off.
- Does not add `run_command`. Not a shell. Does not run anything under `cos/`.
- Does not restore the 12,000-character tool-result cap. `run_check` stdout and stderr are capped at 12,000 each. That is not the tool-result cap.
- Does not commit or push. Do not stage `protocol/step8_self_test.py`.
- Does not start a later slice. Sign-off is a separate instruction.

## What the step 8 sign-off does not do

- Signed off 2026-10-23 by "sign-off". `run_check` passed inside the turn (`fails 0`).
- Does not add `run_command`. Not a shell. Does not run anything under `cos/`.
- Does not restore the 12,000-character tool-result cap.
- Does not commit or push. Do not stage `protocol/step8_self_test.py`.
- Does not start a later slice. Remaining writes, context tools, §3.6, §3.7, and the Claude client stay unstarted.
- Does not start `closed-loop` or `routing-load`.

## What the tools-split sign-off does not do

- Signed off 2026-10-23 by "sign off". Tool bodies are in `cos/tools_impl.py`. The dispatcher reloads that file on each call. Disk test passed (`fails 0`).
- Does not start a later slice. Remaining writes, context tools, §3.6, §3.7, and the Claude client stay unstarted until booked below.
- Does not restore the 12,000-character tool-result cap.
- Does not commit or push. Do not stage `protocol/split_self_test.py` or `protocol/split_cos_tools.py`.
- Does not start `closed-loop` or `routing-load`.

## Step 9 — append_file

**Status:** signed off 2026-10-23 by "sign off". Restart loaded `append_file`. Disk check passed (`fails 0`). Does not start step 10.

**Add:** `append_file(path, content)` only. Creates the file if missing. Returns a summary. Additive. The live tools stay, including `write_file` and `patch_file`.

**Does not add:** `insert_at_line`, `replace_range`, `apply_patches`, copy/move, `create_files`, context tools, `run_command`, or a Claude client.

**Gates:** workspace path only. Cannot edit `cos/tasks.md`. Ask-first still applies. Cap stays off. No shell. No commit.

**Verify:** append to an existing scratch file, create a missing scratch file, refuse `cos/tasks.md`, refuse a path outside the workspace. `write_file` and `patch_file` still work. Then you sign off.

## Step 10 — insert_at_line

**Status:** signed off 2026-10-24 by "sign off". Catalog loaded `insert_at_line`. Disk check passed (`fails 0`). Does not start step 11.

**Add:** `insert_at_line(path, line_number, content)` only. Inserts before the given 1-based line. Returns a summary. Function is in `cos/tools_impl.py`. Name, description, and schema are in `cos/tool_catalog.json`. No dispatcher name chain.

**Does not add:** `replace_range`, `apply_patches`, or anything in §3.4–§3.7.

**Gates:** same as step 9. An out-of-range line errors and does not write. Newline style is preserved. No CR CR LF.

**Verify:** insert before line 1, insert in the middle, insert at end-plus-one, refuse an out-of-range line, refuse `cos/tasks.md`. CRLF stays CRLF. `protocol/step10_self_test.py` exited 0 (`fails 0`). Do not stage that script. Then you sign off.

## Step 11 — replace_range

**Status:** signed off 2026-10-24 by "sign-off". Catalog loaded `replace_range`. Disk check passed (`fails 0`). Does not start step 12.

**Add:** `replace_range(path, start_line, end_line, new_content)` only. Replaces the inclusive line span. Returns a summary. Function is in `cos/tools_impl.py`. Name, description, and schema are in `cos/tool_catalog.json`. No dispatcher name chain.

**Does not add:** `apply_patches` or anything in §3.4–§3.7.

**Gates:** same as step 9. An inverted or out-of-range span errors and does not write. Newline style is preserved. No CR CR LF.

**Verify:** replace a middle span, refuse an inverted span, refuse `cos/tasks.md`. CRLF stays CRLF. `protocol/step11_self_test.py` exited 0 (`fails 0`). Do not stage that script. Then you sign off.

## Step 12 — apply_patches

**Status:** signed off 2026-10-24 by "sign off". Catalog loaded `apply_patches`. Disk check passed (`fails 0`). Does not start a later slice.

**Add:** `apply_patches(patches)` only. `patches` is a list of `{path, search, replace}` or `{path, start_line, end_line, new_content}`. Applied sequentially. Returns a combined summary. Stops on first error and reports what succeeded.

**Does not add:** copy/move, `create_files`, context tools, `run_command`, or a Claude client. This is not a multi-file free write and not a shell.

**Gates:** each patch obeys the same path and `cos/tasks.md` rules. A failed later patch does not roll back earlier successes; the summary names them. Cap stays off.

**Verify:** two successful patches, then a third that fails and stops. A `cos/tasks.md` patch does not write. `protocol/step12_self_test.py` exited 0 (`fails 0`). Do not stage that script. Then you sign off.

## Grok API retry

Signed off 2026-10-24 by "sign-off". Restart loaded `_post_grok`. Disk check passed (`fails 0`). Does not start step 10.

`_post_grok` is the only `requests.post` to the Grok URL. It retries SSL, connection drop, timeout, 429, 500, 502, 503, 504, and an empty `choices` list. Four attempts. Sleeps 2s, 4s, 8s. 401, 403, and other 4xx fail on the first try. A retry stays inside the same tool round. It does not log the key. Disk check `protocol/step_retry_self_test.py` exited 0 (`fails 0`). Do not stage that script.

## Round cap 40

Signed off 2026-10-23 by "sign off". The earlier sign-off died on SSL before it could record. `MAX_TOOL_ROUNDS` is 40 in `cos/cos.py`. Not live until restart. Same stop rules: a hit still asks for one no-tools final answer; a successful push still ends the turn. Does not start step 9. Does not restore the 12,000-character tool-result cap.

## What booking steps 9–12 does not do

- Steps 9–12 are signed off. `append_file`, `insert_at_line`, `replace_range`, and `apply_patches` are live. This booking does not start a later slice.
- Does not start §3.4 (`copy_file`, `move_file`, `create_files`, `file_set_hash`). Those are multi-file utilities, not this slice.
- Does not start context tools, §3.6, §3.7, or the Claude client.
- Does not restore the 12,000-character tool-result cap.
- Does not commit or push.
- Does not start `closed-loop` or `routing-load`.
- Does not start the external tool catalog. That is step 13. Booking it does not implement it.

## Step 13 — External tool catalog

**Status:** signed off 2026-10-23 by "sign off". Restart loaded the catalog. Disk check fails 0. Does not start step 10.

**Why.** Tool bodies reload from `cos/tools_impl.py` on each call. The catalog does not. `TOOLS`, the `execute_tool` name chain, and the system-prompt tool list are built once in `cos/cos.py`. That is why step 9 had to edit the dispatcher to register `append_file`, and why that registration is not live until restart.

**Written:** `cos/tool_catalog.json` (version 1, 17 tools). `load_tool_catalog` reads it before each Grok round, not only at startup. A bad load keeps the last good list, else the in-process `TOOLS` fallback. It does not drop tools. Catalog dispatch is in `cos/cos.py` (`_dispatch_tool`, `_bind_tool_args`). A catalog name calls the same name in the reloaded `cos/tools_impl.py`. `git` and `handoff` stay special. Signed off 2026-10-24 by "sign-off". Restart loaded it. Disk check fails 0. Does not start step 10.

**Does not add:** a new file tool, `run_command`, a Claude client, or a reload of `cos/cos.py` itself. A write to the dispatcher still needs one restart. `run_check` still refuses anything under `cos/`.

**Order:** next after the remaining-writes slice. It does not jump ahead of step 9 sign-off, and it does not start steps 10–12. Those stay in order.

**Gates:** ask-first still applies. Existing tools stay, same names and gates. Cap stays off. No shell. No commit from this booking.

**Verify, when implemented:** a catalog edit is visible on the next Grok round without restarting. A bad catalog file does not drop the last good list. `append_file` and the sixteen earlier tools stay registered. Then you sign off.

## What booking step 13 does not do

- Does not write the catalog file. Does not edit `cos/cos.py` or `cos/tools_impl.py`.
- Does not make `append_file` live. That still needs a restart, then a sign-off. Sign-off does not start step 10.
- Does not start steps 10–12. Each still needs the previous sign-off and its own instruction.
- Does not start §3.4, context tools, §3.6, §3.7, or the Claude client.
- Does not restore the 12,000-character tool-result cap.
- Does not commit or push.
- Does not start `closed-loop` or `routing-load`.
