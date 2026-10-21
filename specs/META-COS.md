# Local Grok-Directed Agentic Chief of Staff
## Complete Specification: Concept to Implementation Plan

**Version 1.0 — Bootstrap Specification**  
**21 October 2026**

This document is the governing meta-specification for a fully local, self-improving agentic Chief of Staff that directs all project work, routes tasks to appropriate executors (Grok API, Claude), and maintains inspectable state on the user's machine and in GitHub.

---

## 1. Purpose

A single local process that serves as the sole agent the user interacts with for directing work. It owns task breakdown, prioritization, specification integrity, and acceptance criteria. It routes work to the most appropriate executor and maintains durable, inspectable state entirely under user control.

**Primary responsibilities:**

- Accept high-level directives from the user and translate them into actionable, spec-referenced tasks.
- Decide which executor (Grok API, Claude, or specialist agent) should perform each task.
- Maintain and enforce written specifications as the source of truth.
- Log every significant decision, hand-off, and result in an append-only local event log.
- Propose and (after acceptance) apply improvements to its own routing, prompts, specs, and processes.
- Keep all durable state on the local machine and in the user's GitHub repositories.

## 2. Non-Goals

- Does not replace Android Studio, PlatformIO, Git, or other development tools.
- Does not hold long-term private state in any cloud service.
- Does not perform unattended commits or pushes without explicit policy or user approval.
- Does not act as a general personal assistant outside defined project scopes.
- Does not require continuous cloud connectivity for core operation (cloud models are optional tools).

## 3. Core Principles

- **Spec-driven**: Every non-trivial action references or updates a written specification.
- **Hierarchical**: The COS directs; specialist agents and models execute.
- **Adversarial where useful**: Implementation and review are separated when it improves quality.
- **Local-first**: Orchestrator, memory, routing logic, and event log run on the Windows machine.
- **Inspectable**: Every decision, hand-off, and result is logged and reviewable.
- **Self-improving at all levels**: The COS, its routing policy, prompts, specialist agents, project specs, and memory schema are all expected to improve over time through explicit, logged, reviewable changes. Self-improvement is a normal, first-class activity.
- **Minimal bootstrap**: Start with the smallest reliable loop; expand only after the core is proven.

## 4. Architecture

The COS is a local Python process. Cloud models are called as stateless tools. All continuity comes from the local event log and GitHub.

```
User
  │
  ▼
Local COS (Python process)
  ├── Router (decides executor)
  ├── Memory (local JSONL event log + GitHub)
  ├── Spec manager
  └── Delegates to:
        ├── Grok API                    — default for reasoning, protocol, review
        ├── Claude (API or hand-off)    — heavy implementation
        └── Specialist local agents     — later
```

**Durable state locations:**

- Local append-only JSONL event log (under the OneDrive-synced project root).
- GitHub repository (specs, protocol files, code, issues, pull requests).
- Event-log snapshots are pushed to the GitHub repo at major checkpoints.

**Environment notes:**

- The COS workspace lives under the user's OneDrive-synced projects root. OneDrive provides continuous replication (sync delay is acceptable).
- Each project folder is also a local Git repository rooted on GitHub.
- No local LLM is present at bootstrap. Grok API is the default executor until a local model is added later.

## 5. Components

| Component | Responsibility | Location |
|-----------|----------------|----------|
| COS process | Main loop, user interface, routing, logging | Local Python |
| Router | Chooses executor based on task type and policy | Inside COS |
| Memory / event log | Ordered record of goals, decisions, hand-offs, results | Local JSONL |
| Spec store | Current specifications and acceptance criteria | GitHub + local cache |
| Grok client | Calls Grok API for designated reasoning tasks | Local |
| Claude interface | API call or self-contained prompt for VS Code | Local |
| Specialist agents | Narrow tools (PID enumeration, template linter, etc.) | Local functions/processes (later) |
| GitHub interface | Read/write issues, PRs, files | Local (gh CLI or library) |

## 6. Routing Policy (Initial)

No local LLM exists at bootstrap. Grok API is the default for all reasoning and review tasks. Routing rules are versioned (e.g., `routing.yaml`) and may be updated by the COS after logged acceptance.

| Task Type | Preferred Executor | Fallback |
|-----------|--------------------|----------|
| Status, summarization, simple file ops | Grok API | — |
| Protocol design, PID lists, Android Auto template legality, adversarial review | Grok API | — |
| Multi-file Kotlin / Android implementation | Claude | Grok API (smaller pieces) |
| OBD command sequences, parsing | Grok API or specialist | — |
| GitHub mechanical updates | COS directly | — |
| Private or offline-only work | (deferred until local LLM) | Grok API |

When a local LLM is later added, simple/fast tasks can be shifted to it via a normal self-improvement change.

## 7. Memory and State

- Append-only JSONL event log on local disk (inside the OneDrive-synced workspace). Each event contains: timestamp, type, actor, summary, and references (spec section, GitHub link, file paths).
- Specs and protocol files in GitHub are the authoritative shared state.
- COS loads relevant spec sections and recent events at the start of each user turn.
- No reliance on cloud-side conversation memory for continuity.
- Event log schema is itself versioned and may be improved (see Self-Improvement).
- Continuous replication is provided by OneDrive. Major checkpoints of the event log are committed and pushed to the GitHub repo.

## 8. Interaction Model

- User issues high-level directives to the COS only.
- COS responds with: current understanding, proposed breakdown, chosen executor, and any required spec updates.
- For Claude hand-off: COS emits a complete, self-contained prompt plus file list that the user can paste into a VS Code Claude session (or sends via API if configured).
- All significant actions are logged before and after execution.
- User can inspect the event log and override any routing decision at any time.

## 9. Self-Improvement at All Levels

Self-improvement is fundamental and applies recursively to every layer of the system.

| Level | What Can Be Improved | Mechanism |
|-------|----------------------|-----------|
| COS routing & policy | routing.yaml, decision rules, model choice | Propose → log → accept |
| COS prompts & templates | System prompts, hand-off templates, review checklists | Versioned in repo |
| Memory / event schema | JSONL fields, captured data | Explicit migration event |
| Project specs | SPEC.md, acceptance criteria, protocol JSON | Propose after execution; user approves |
| Specialist agents | OBD parser, template linter, etc. | Replace when validated |
| Delegation quality | When to use which model | Measured by rework; rules updated |

**Required mechanisms (minimum for bootstrap):**

- Every self-modification is an explicit event in the local JSONL log.
- Specs and routing rules are versioned in GitHub.
- The COS may propose improvements to its own behavior but cannot silently adopt them; adoption requires a logged acceptance step.
- After significant tasks the COS may append an improvement note.
- This meta-spec itself may be revised; revisions are committed and logged.

## 10. First Project Context: 2019 Toyota 4Runner Android Auto App

The initial project the COS will direct is a custom Android Auto application for a 2019 Toyota 4Runner Limited 4WD. The app reads live data from an OBDLink MX+ Bluetooth adapter, combines it with phone GPS/sensors, and projects driver-safe dashboards and map-integrated views to a Hamilton Conversion Kit via the Android for Cars App Library.

**Key technical constraints the COS must enforce:**

- Car App Library templates only (PaneTemplate, ListTemplate, NavigationTemplate, etc.).
- Distraction rules: glanceable, low-density UI while driving; richer content only when parked.
- OBD connection lives entirely on the phone (Bluetooth to MX+); Hamilton is a pure projection surface.
- Protocol definitions (standard + Toyota enhanced PIDs) live in versioned JSON.
- PID enumeration is a parked-only or bench function.
- Map integration uses phone GPS; OBD values appear in panes or status areas alongside the map.
- Android Auto UI and service layer are Kotlin (Car App Library requirement). C++ may be used via NDK for optional native components. Protocol remains JSON.

## 11. Bootstrap Sequence

1. Create the COS workspace folder under the OneDrive-synced projects root and initialize it as a GitHub repository. (Completed.)
2. Commit this document as `specs/META-COS.md`.
3. Implement a minimal COS Python process that can: read this meta-spec and a project spec; append to a local JSONL event log; call the Grok API; emit a self-contained Claude hand-off prompt; update a markdown task list or GitHub issue.
4. Write the first project specification (4Runner Android Auto app) that references this meta-spec.
5. Run a closed-loop test: user asks the COS to produce the initial protocol JSON and CarAppService skeleton; COS routes, executes or hands off, logs the result, and reports.
6. Only after the loop is reliable, add specialist agents, automatic Claude API calls, or additional projects.
7. Begin logging improvement notes and applying accepted changes to routing and prompts.
8. At major phase boundaries, commit and push a checkpoint of the event log to GitHub.

## 12. Acceptance Criteria for the Bootstrap

- COS runs entirely locally and survives restarts via its event log and GitHub state.
- User can issue a high-level task and receive a routed plan without interacting with other models directly.
- At least one successful Grok API call and one Claude hand-off have been logged against the 4Runner project.
- Routing decisions are visible in the event log and overridable by the user.
- No required state lives only in a cloud chat session.
- At least one self-improvement note has been logged and is reviewable.
- Event log is replicated by OneDrive and checkpointed to GitHub at major milestones.

## 13. Implementation Plan

### 13.1 Repository Layout

```
cos-workspace/
├── specs/
│   ├── META-COS.md          (this document)
│   └── 4runner-aa-obd.md    (first project spec)
├── protocol/
│   └── toyota-4runner-2019.json
├── cos/
│   ├── cos.py               (main process)
│   ├── routing.yaml
│   ├── prompts/
│   └── event_log.jsonl
├── android/                 (Car App Library project, later)
└── tools/                   (bench scripts, PID enumeration)
```

### 13.2 Minimal COS Process (v0)

A single Python script providing a REPL or simple CLI. On each user input it:

1. Loads recent events and relevant spec sections.
2. Calls the router.
3. Either handles the request, calls Grok API, or emits a Claude prompt.
4. Logs the full exchange.
5. Optionally proposes a spec or routing update.

### 13.3 Event Log Schema (initial)

```json
{
  "ts": "2026-10-21T12:00:00Z",
  "type": "user_directive | routing_decision | grok_call | claude_handoff | result | improvement_note | spec_update",
  "actor": "user | cos | grok | claude",
  "summary": "short description",
  "refs": ["specs/META-COS.md#9", "protocol/toyota-4runner-2019.json"],
  "detail": {}
}
```

### 13.4 Phased Delivery

- **Phase 0 — Scaffold**: Repo layout, this meta-spec committed, empty event log, routing.yaml stub. (In progress.)
- **Phase 1 — Minimal loop**: COS can read specs, log events, call Grok API, emit Claude hand-off. Closed-loop test on protocol JSON generation.
- **Phase 2 — 4Runner project spec**: Full project specification written; COS directs creation of protocol JSON and CarAppService skeleton.
- **Phase 3 — Self-improvement path**: Improvement notes are captured; at least one accepted change to routing or prompts is applied and logged.
- **Phase 4 — Specialists & expansion**: Add OBD enumeration specialist, template compliance checker; support additional projects.

## 14. Open Decisions

- Claude access method for v1: API key versus prompt hand-off only.
- Precise event-log schema fields beyond the initial sketch.
- Whether the COS may open pull requests directly or only prepare them.
- Initial scope boundary: single project (4Runner app) versus multi-project from day one.
- Policy for automatic acceptance of low-risk self-improvements versus always requiring user confirmation.
- Local LLM choice and integration timing (deferred).

## 15. Glossary

- **COS**: Chief of Staff — the local directing agent defined by this specification.
- **Car App Library**: Android for Cars App Library; the supported way to build Android Auto projected UIs.
- **Hamilton Conversion Kit**: Aftermarket head unit that receives Android Auto projection.
- **OBDLink MX+**: Bluetooth OBD-II adapter with enhanced Toyota/Lexus PID support.
- **Event log**: Append-only local JSONL file that records all significant COS activity. Replicated by OneDrive; checkpointed to GitHub.
- **Hand-off prompt**: A self-contained prompt generated by the COS for pasting into a Claude VS Code session.
- **Kotlin**: JVM language required for the Android Auto Car App Library layer. Not syntactic sugar over Java; has its own compiler.

---

*— End of Specification —*

This document is intended to be committed as `specs/META-COS.md` and to serve as the governing artifact for the bootstrap and all subsequent evolution of the local COS.
