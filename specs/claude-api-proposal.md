# Direct Claude API Calls
## Proposal

**Status:** proposed, not accepted  
**Date:** 22 October 2026  
**Does not implement anything.** This file is a decision record. It does not add a client, load a key, change routing, or call Claude.

**Governs if accepted:** how the local COS invokes Claude, and only that.  
**Governed by:** `specs/META-COS.md`. Conflicts resolve in favor of META-COS non-goals, then this proposal is revised.  
**Also constrained by:** `cos/prompts/ask-first.md` (locked). This proposal does not relax ask-first.

Related open decision: META-COS §14, "Claude access method for v1: API key versus prompt hand-off only."  
Related project line: `specs/4runner-aa-obd.md` §12, "Claude access: prompt hand-off only for v1."  
Related open task: `routing-load`. This proposal does not close it.

---

## 1. What is true now

- `cos/cos.py` calls only Grok, using `GROK_API_KEY` from the environment, against `https://api.x.ai/v1/chat/completions`.
- Claude is hand-off only. The `handoff` tool prints a self-contained prompt for the user to paste into a VS Code Claude session. It does not call Claude and does not read a key.
- `cos/routing.yaml` names Claude for `kotlin`, `android`, `implement`, and `carapp`. The process never loads that file.
- META-COS §11 step 6 says automatic Claude API calls come only after the minimal loop is reliable. Bootstrap acceptance (§12) wants one logged Claude hand-off, not one API call.
- No Anthropic client, no second model endpoint, and no Claude key exist in this process. This proposal does not fetch, store, or invent a key.

## 2. What keeps a direct call from happening

1. **Not built.** There is no Claude client, no tool, and no second request path.
2. **Open decision.** META-COS §14 and the project spec §12 still say hand-off only for v1.
3. **Bootstrap order.** Automatic Claude API calls are step 6, after the closed loop is reliable. The closed loop has not been run.
4. **No key in the process.** Even after a client exists, a call cannot run until the user creates a key in their own Anthropic account and puts it in the environment. The COS must not obtain one.
5. **Ask-first.** A preference ("it would be better if") is not permission to add the client. Acceptance of this proposal is not permission either, unless the acceptance directive explicitly says to implement it.
6. **Routing is still a stub.** A client with no loaded policy would hard-code when Claude runs. That repeats the current gap in a worse place.

## 3. Proposal

Add a bounded Claude client later, as a stateless tool, on the same pattern as the Grok client. Keep hand-off. Do not make Claude the default executor.

**Call shape**

- Environment variable: `ANTHROPIC_API_KEY`. Read it the way `GROK_API_KEY` is read. Never write it to the event log, `cos/context.md`, a spec, or a prompt file.
- Endpoint and model are pinned in code and named in the event detail, not improvised per turn. Exact model id is chosen at implementation time from the user's account, not guessed here.
- One request, one completion. No cloud-side memory. Continuity stays in the local event log and GitHub.
- Timeout and a response-size cap, same discipline as the Grok call.
- Log `claude_call` with timestamp, model name, prompt preview, reply preview, and byte counts. Do not log the key or the full prompt if it contains secrets.

**When it may be called**

- Only after `routing-load` is done, so the process reads `cos/routing.yaml` and the route actually says Claude.
- Only for a task the current user directive explicitly assigned to Claude, or for an explicit "call Claude for X".
- Ask-first still wins. An open task, a routing match, or this proposal is not a work order.
- Grok remains the default for protocol, PID review, template legality, status, and spec work (`specs/4runner-aa-obd.md` §11).

**What stays**

- `handoff` stays. It is the fallback when the key is missing, the call fails, or the user wants to paste into VS Code.
- Bootstrap acceptance is still one logged hand-off. An API call does not replace that check unless META-COS §12 is revised and accepted.
- Git gates stay. Claude output is text in the workspace. Commit still needs `approve git commit`. Push still needs `approve git push`.
- No general shell. The client is HTTPS to the Anthropic API only, not a way to run local commands.

## 4. What this proposal refuses

- Fetching, creating, or embedding an API key.
- Putting the key in `cos/context.md`, the JSONL log, Git, or a hand-off prompt.
- Silent cutover from hand-off to API.
- Letting Claude commit, push, or run git.
- Using Claude for read-only protocol work that the project spec assigns to Grok.
- Implementing the client in the same turn as accepting this document.

## 5. Acceptance

This document is accepted only by an explicit user instruction that accepts it (for example "accept the Claude API proposal"). Until then it is a proposal.

Acceptance logs an event and updates the status line of this file. It does not by itself:

- revise META-COS §14,
- revise `specs/4runner-aa-obd.md` §12,
- load `cos/routing.yaml`,
- add a client to `cos/cos.py`,
- or authorize a live call.

Those are separate explicit instructions.

## 6. Implementation order, after acceptance

Do not start this list until the user says to implement it.

1. User creates the key in their own Anthropic account and sets `ANTHROPIC_API_KEY` in the environment. COS never sees the key value in a directive.
2. Finish or explicitly defer `routing-load`, so a Claude route is data, not a hard-coded guess.
3. Add the client and a `claude_call` log type. Missing key returns a clear error and points at hand-off. No retry loop that spends the key on failures.
4. Keep `handoff` working. Add a task-list note that API is available and hand-off remains the fallback.
5. One logged test call on a tiny, non-secret prompt, only under an explicit "call Claude" directive. Not the CarAppService skeleton unless that task is the one named.
6. Only then consider using the API for `closed-loop`. The hand-off path remains valid for that acceptance check.

## 7. Open decisions this proposal does not close

- Model id and max tokens.
- Whether a failed API call should automatically emit a hand-off prompt, or stop and report.
- Whether META-COS §12 should eventually count an API call as the Claude acceptance check.
- Cost cap per turn. Default if implemented: one Claude call per user turn, same spirit as one git push per turn.

---

*— End of Proposal —*
