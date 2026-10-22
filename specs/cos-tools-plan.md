# COS Tools — Initial Implementation Plan

**Status:** steps 1–2 done and signed off; steps 3–6 not started
**Booked:** 2026-10-22, by the explicit directive "Book this initial plan"
**Step 1 done:** 2026-10-22, by "do step 1". User signed off the same day: v1.2 matched the review.
**Plan updated:** 2026-10-22, by the explicit directive "Update the spec files and plan"
**v1.3 sign-off:** 2026-10-22, by the explicit directive "I approve". Does not start step 2.
**Step 2 done:** 2026-10-22, by "do step 2". User signed off the same day: "I sign off step 2". Script passed (`fails 0`). Does not start step 3.
**Parent spec:** specs/cos-tools.md v1.3 (v1.2 and v1.3 signed off; v1.3 sign-off does not start step 2)
**Related:** specs/claude-api-proposal.md (proposal only; client not built)
**Does not authorize:** editing cos/cos.py for a new slice, implementing step 3, calling Claude, or git commit/push

This file records the ordered plan so progress can be made one step at a time. Booking it was not approval to start a step. Updating it is not approval to start a step. Signing off a finished step does not start the next one. Each step needs its own explicit instruction that names that step. Ask-first still holds.

This update records the step 2 sign-off. It does not start step 3.

## Rules for every step

- One step per explicit instruction. Do not start the next step in the same turn.
- Test, verify, and sign off before the next step is eligible.
- Sign-off is an explicit user instruction, not a COS self-check.
- Do not implement section 3.6 or 3.7 in steps 1–6. Those sections stay **on hold**.
- Do not build the Claude API client in this plan. The tool surface is shared so that client can use it later.
- Do not weaken ask-first, the git approval phrases, or META-COS §2.
- No general shell, no unattended commit or push, no key handling, no paths outside the workspace.
- **Existing tools first.** `list_dir`, `read_file`, `write_file`, `update_task`, bounded `git`, and `handoff` stay registered under those names and gates. A slice may add tools beside them. It may not remove, rename, unregister, or replace them. Full-file `read_file` and `write_file` stay. The tool loop, deep context, event log, manual commands, and console wrap stay. A failure in a new tool must not take the six down. Detail is `specs/cos-tools.md` §2.3.
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

**Cap:** `TOOL_RESULT_CHAR_LIMIT` is 12,000. A truncated result says `TRUNCATED` and how many characters were omitted. The file on disk is unchanged. Full-file `read_file` stays.

**Verify:** met. `python protocol/step2_self_test.py` printed thirteen `PASS` lines and `fails 0`. That covered the tool list, the cap, truncation, an unchanged short result, `file_info` size/sha256/mtime, markdown headings, JSON keys, Python names, path escape, `read_file`, and the read cap. The user signed off the same day.

**Done when:** met. Those checks passed and the user signed off. Step 3 was not started in that turn.

**Not part of the product:** `protocol/step2_self_test.py` is a hand checklist. Do not stage it. `cos/file_info.py` is an empty leftover. The live functions are in `cos/cos.py`.

## Step 3 — Partial reads

**Task id:** `tools-3-read`
**Status:** eligible, not started. Needs its own instruction. The step 2 sign-off is not that instruction.
**Was blocked on:** step 2 sign-off. That block is lifted. The no-loss rule still applies.

Add `read_file_range`, `read_file_head`, `read_file_tail`, and `search_in_file`. Inclusive 1-based ranges. Return "lines X–Y of Z" on a range read. Results obey the cap from step 2.

These are additions. Full-file `read_file` stays available. The six live tools stay. `file_info` and `file_outline` stay. No search across files, no patch, no shell, no Claude client in this step.

**Verify:** a range read of `specs/META-COS.md` returns only the requested lines; head and tail honor `n`; a single-file search returns matching lines with context; an out-of-range request errors instead of reading the whole file; full-file `read_file` still returns the file (subject to the cap and its truncation notice).

**Done when:** those checks pass and you sign off. Full-file `read_file` stays available. Do not start step 4 in the same turn.

## Step 4 — Search across files

**Task id:** `tools-4-search`
**Blocked on:** step 3 sign-off.

Add `search_files` and `list_files` (simple glob, recursion off unless asked). In-workspace only. Results obey the cap. These do not replace `list_dir`.

**Verify:** a search hits a known string in `specs/` and misses a string that is not there; glob `*.json` under `protocol/` returns the protocol file and does not leave the workspace; `.git` is not expanded; `list_dir` still works.

**Done when:** those checks pass and you sign off.

## Step 5 — One-file patch

**Task id:** `tools-5-patch`
**Blocked on:** step 4 sign-off.

Add `patch_file` with literal search/replace. If `replace_all` is false and the search occurs any number other than 1, return an error and do not write. Return a short diff summary. Do not add `apply_patches`, `replace_range`, or multi-file writes in this step. Do not replace `write_file`.

**Verify:** a unique literal patch on a scratch file succeeds and the diff matches the change; a non-unique search does not write; a missing search does not write; `write_file` still replaces a file; `cos/tasks.md` remains updatable only via `update_task`.

**Done when:** those checks pass and you sign off. `cos/tasks.md` remains updatable only via `update_task`.

## Step 6 — Sign-off gate for anything further

**Task id:** `tools-6-gate`
**Blocked on:** step 5 sign-off.

No new tool. Record that steps 1–5 are the first wave, and that the next slice is a separate decision.

Still not started, and not implied by finishing step 5:

- Remaining §3.3 and §3.4 writes (`append_file`, `insert_at_line`, `replace_range`, `apply_patches`, copy/move, `create_files`).
- §3.5 context tools (`context_stats`, `summarize_context`).
- §3.6 execution tools. **On hold.** Includes `run_command`. Not a general shell.
- §3.7 internet tools. **On hold.**
- Claude API client. Separate proposal. Not built by this plan.
- System-prompt paragraph in `specs/cos-tools.md` §4. Add it only when the tools it names actually exist, and only under an explicit instruction.

**Done when:** this gate is written into the task list as the end of the first wave, and you sign off. The next slice still needs its own instruction. The six live tools are still present. `file_info` and `file_outline` stay.

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

- Does not start step 3. `tools-3-read` stays open and not doing.
- Does not add partial reads, search, or patch.
- Does not edit `cos/cos.py`.
- Does not commit or push.
