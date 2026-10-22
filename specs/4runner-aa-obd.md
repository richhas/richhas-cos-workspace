# 2019 Toyota 4Runner Android Auto OBD App
## Project Specification

**Version 0.1 — Initial project spec**  
**22 October 2026**

**Governs:** the 4Runner Android Auto application, `protocol/toyota-4runner-2019.json`, and related bench tools.  
**Governed by:** `specs/META-COS.md` (Local COS). This spec does not override the meta-spec. Conflicts are resolved in favor of META-COS non-goals and principles, then recorded as an open decision here.

Bootstrap reference: META-COS §11 step 4 and §13.1.

---

## 1. Purpose

Build a custom Android Auto application for a 2019 Toyota 4Runner Limited 4WD that shows live vehicle data and a map-integrated, driver-safe view on a Hamilton Conversion Kit.

The phone owns the OBD connection (Bluetooth to an OBDLink MX+). The Hamilton head unit is a projection surface only. Phone GPS and sensors supply location. OBD values appear in Car App Library panes or status areas alongside the map. Protocol definitions live in versioned JSON, not in Kotlin constants.

The local COS directs this work. It does not replace Android Studio, PlatformIO, or Git (META-COS §2).

## 2. Non-Goals

- Not a full diagnostic scanner, ECU flash tool, or bidirectional control app.
- Not a replacement for the factory head unit or for Toyota safety systems.
- Does not write to the vehicle bus (no actuator tests, no mode 08 control, no key programming).
- Does not run PID enumeration while driving.
- Does not invent Toyota enhanced PID formulas. Unverified PIDs are labeled and excluded from the driving UI.
- Does not require a local LLM (META-COS §4). Grok is the default executor; Claude is for multi-file Kotlin.
- Does not store trip history in a cloud service. Local files and the user's GitHub repo only.

## 3. Vehicle and Hardware

| Item | Requirement |
|------|-------------|
| Vehicle | 2019 Toyota 4Runner Limited 4WD |
| Adapter | OBDLink MX+ over Bluetooth. Enhanced Toyota/Lexus PID support is a capability of the adapter, not a license to invent formulas. |
| Phone | Android phone that supports Android Auto projection and Bluetooth. Exact min SDK is an open decision (§12). |
| Head unit | Hamilton Conversion Kit. Receives Android Auto projection. No OBD radio on the head unit. |
| Location | Phone GPS. The head unit is not a location source. |
| Bus access | Read-only OBD-II requests from the phone. |

## 4. Principles (project)

- **Spec before PIDs.** No protocol JSON field is authoritative until this spec says how it is validated.
- **Read-only.** Requests are queries. No control commands ship in the app.
- **Parked vs driving.** Enumeration, settings, and dense lists are parked-only. Driving UI is glanceable.
- **JSON is the protocol.** Kotlin parses the JSON. It does not embed a second copy of PID definitions.
- **Source every enhanced PID.** Each Toyota enhanced entry names its source, status, and the date it was confirmed on this vehicle or marked unverified.
- **Templates only.** UI uses the Android for Cars App Library. No custom Android Auto surface drawn outside supported templates.

## 5. Architecture

```
Phone
  ├── Car App (Kotlin, Car App Library)     projected to Hamilton
  ├── OBD session (Bluetooth → OBDLink MX+) read-only
  ├── Protocol loader                       protocol/toyota-4runner-2019.json
  └── Location                              phone GPS / sensors

Hamilton Conversion Kit                     projection only
```

- Android Auto UI and the car service layer are Kotlin (Car App Library requirement).
- C++ via NDK is optional and out of scope until a measured need exists.
- Protocol file path: `protocol/toyota-4runner-2019.json`.
- COS routes protocol, PID lists, template legality, and review to Grok. Multi-file Kotlin goes to Claude as a hand-off unless an API path is later accepted (META-COS §6, §14).

## 6. Driving and Parked Behavior

**While driving (car restriction level is not parked):**

- Allowed templates: `NavigationTemplate` (map plus minimal action strip), `PaneTemplate` or equivalent glanceable pane, `MessageTemplate` for faults.
- At most a handful of live values. Initial driving set is specified in §8. No scrolling PID browser.
- No enumeration, no settings editors, no raw hex dumps.
- Text must be short. No paragraphs. No video or animated gauges that require sustained gaze.

**While parked:**

- Richer `ListTemplate` / `PaneTemplate` screens are allowed: PID browser, connection status, protocol version, last error.
- PID enumeration (broadcast scan or Toyota enhanced discovery) runs only in this state, or on the bench with no driving session.
- The app must re-check the car restriction level before starting an enumeration and must abort if the vehicle leaves parked.

Distraction rules follow the Android for Cars driver-distraction guidance. If a template is not legal for the current restriction level, it is not shown. COS review of template choice is a Grok task (routing: template, review).

## 7. OBD Session

- Connection target: OBDLink MX+ Bluetooth. Pairing UI may be a phone activity outside the car projection, not a driving template.
- Session is owned by the phone process. The projected car screen only displays session state (connected, connecting, failed, parked-only).
- Transport: ELM-style AT command setup, then OBD requests. Exact init string is an implementation detail but must be recorded in the protocol JSON `adapter` object once confirmed on hardware, not guessed in Kotlin.
- Requests are serialized. One outstanding request at a time in v1.
- Timeouts, no-data, and `STOPPED`/`UNABLE TO CONNECT` are surfaced as status, not as fake live values.
- The app never sends mode 08 or other control modes. The protocol file must not contain write or actuation commands.
- Poll rates: driving UI polls the §8 set at a modest rate (default 1 Hz for the set, not 1 Hz per extra PID). Parked browser may poll the selected PID slower. Exact milliseconds are an open decision (§12) and must be in the JSON once chosen.

## 8. Live Data Scope (v1)

v1 driving values, all standard SAE J1979 mode 01 unless noted:

| UI label | Mode | PID | Notes |
|----------|------|-----|-------|
| RPM | 01 | 0C | Engine speed |
| Speed | 01 | 0D | Vehicle speed as reported by ECU; GPS speed is separate and labeled GPS |
| Coolant | 01 | 05 | Engine coolant temperature |
| Load | 01 | 04 | Calculated engine load |
| Throttle | 01 | 11 | Absolute throttle position |

Parked-only extras allowed in the browser once defined in JSON:

- Intake air temperature (01 0F), control module voltage (01 42), fuel level (01 2F) if the ECU supports them.
- Toyota enhanced candidates (transmission temperature, ATF, individual wheel speeds, 4WD range) only after a source and a bench or parked confirmation. Until then their `status` is `unverified` and they are not polled.

GPS fields (phone, not OBD): latitude, longitude, speed, bearing. Shown as GPS, never mixed into an OBD PID entry.

Support is per ECU response. A PID that returns NO DATA is marked unsupported for this vehicle in the session; it is not shown as zero.

## 9. Protocol JSON Contract

File: `protocol/toyota-4runner-2019.json`  
This section is the acceptance contract for the `protocol-json` task. The file must validate against this section before any Kotlin parser is written.

### 9.1 Top-level fields

| Field | Required | Rule |
|-------|----------|------|
| `schema` | yes | String. Start at `"4runner-obd-protocol/1"`. |
| `vehicle` | yes | Object: `year` 2019, `make` Toyota, `model` 4Runner, `trim` Limited, `drivetrain` 4WD. |
| `adapter` | yes | Object: `model` OBDLink MX+, `transport` bluetooth. `init` is an array of strings; empty until confirmed on hardware. |
| `sources` | yes | Array of `{id, title, note}`. Every PID `source` refers to an id here. |
| `pids` | yes | Array of PID objects (§9.2). Must include the five v1 driving PIDs. |
| `poll_groups` | yes | At least `driving` and `parked`. `driving` lists only verified v1 PIDs. |
| `commands_forbidden` | yes | Includes mode 08 and any actuation. Documents the read-only rule. |

### 9.2 PID object

| Field | Required | Rule |
|-------|----------|------|
| `id` | yes | Stable slug, e.g. `rpm`, `coolant_temp`. |
| `name` | yes | Short display name. |
| `mode` | yes | Hex string, e.g. `"01"`. |
| `pid` | yes | Hex string, e.g. `"0C"`. |
| `header` | no | ECU header if a non-default header is required. Omit for standard mode 01. |
| `formula` | yes if status is `verified` | Expression over response bytes `A`, `B`, `C`, `D` only. Standard J1979 formulas are allowed. |
| `units` | yes | e.g. `rpm`, `km/h`, `celsius`, `percent`. |
| `bytes` | yes | Expected data byte count. |
| `status` | yes | `verified` or `unverified`. |
| `source` | yes | Id in `sources`. |
| `driving_ok` | yes | Boolean. True only if status is `verified` and the PID is in the §8 driving set. |
| `notes` | no | Caveats. Required when status is `unverified`. |

### 9.3 Hard rules

- Do not invent Toyota enhanced formulas, headers, or scaling. If the formula is not in a cited source or not confirmed on this vehicle, `status` is `unverified`, `formula` is omitted, and `driving_ok` is false.
- Standard J1979 mode 01 formulas used in §8 may be status `verified` with source id `sae-j1979`. That is not a claim that this ECU implements every standard PID.
- No duplicate `id`. No duplicate (`mode`, `pid`, `header`) triple.
- `poll_groups.driving` must not reference an unverified PID.
- The JSON file is data. No Kotlin, no shell, no URLs that the app fetches at runtime.

### 9.4 Source ids (initial)

| id | Use |
|----|-----|
| `sae-j1979` | Public standard mode 01 definitions for the §8 set. |
| `obdlink-mx-docs` | Adapter behavior only, once cited. Not a PID formula source by itself. |
| `bench-2019-4runner` | Confirmation on this vehicle. Required before an enhanced PID becomes `verified`. |
| `unverified-candidate` | Placeholder for a named candidate with no formula. |

## 10. App Surfaces (v1)

Minimum projected screens:

1. **Map** — `NavigationTemplate`, phone GPS, action strip with no more than the driving values that fit a glance, or a single action that opens the pane.
2. **Gauges** — `PaneTemplate` with the §8 driving set and connection status.
3. **Parked browser** — list of protocol PIDs with status; unverified rows are visibly marked and are not polled until verified.
4. **Fault** — `MessageTemplate` for Bluetooth loss or adapter failure, with a retry action.

No custom cluster drawing in v1. Skeleton class for the closed-loop test: `CarAppService` plus a `Session` that can show the fault or gauge pane when the car is connected, and that loads the protocol JSON from assets (copy of `protocol/toyota-4runner-2019.json`). The skeleton is a Claude hand-off, not a COS shell build (task `closed-loop`).

## 11. COS Routing for This Project

| Work | Executor | Notes |
|------|----------|-------|
| This spec, protocol JSON, PID review, template legality | Grok | Default. Must follow §9. Must not invent enhanced PIDs. |
| Multi-file Kotlin / Android implementation | Claude hand-off | COS emits a self-contained prompt and file list. |
| Status, summarization, task list | Grok | |
| Git commit / push | User | COS does not commit or push unless a later accepted policy says so. |
| PID enumeration on hardware | Specialist, later | Parked or bench only. Not in bootstrap. |

`cos/routing.yaml` already matches this table but is not loaded by the process (task `routing-load`). Until that task is done, the COS still follows this table and META-COS §6 manually.

## 12. Open Decisions

- minSdk / targetSdk / Car App Library artifact version. Decide before the skeleton is treated as buildable. The hand-off may pin a current stable Car App Library and say so in the prompt.
- Claude access: prompt hand-off only for v1 (META-COS §14).
- Poll interval milliseconds and Bluetooth reconnect backoff. Record in JSON when chosen.
- Whether GPS speed or ECU speed is the primary speed label when they disagree. Default: show ECU speed as Speed and GPS speed only on the map.
- Units: metric internally (°C, km/h). Display unit preference is parked-only and out of v1 if it risks clutter.
- Automatic acceptance of low-risk spec edits: not allowed. Spec updates are proposed, logged, and accepted (META-COS §9).

## 13. Acceptance Criteria

- `specs/4runner-aa-obd.md` exists and references `specs/META-COS.md`.
- A reader can implement `protocol/toyota-4runner-2019.json` from §9 without inventing enhanced PIDs.
- Driving UI constraints are testable: template names, driving PID set, parked-only enumeration.
- Read-only rule is explicit enough to reject a protocol file that contains mode 08.
- COS task `protocol-json` can proceed from this document.
- Kotlin skeleton is out of scope for this spec's own acceptance; it is task `closed-loop`.

## 14. Phased Delivery

1. **Spec (this document).** Unblocks protocol JSON.
2. **Protocol JSON** against §9. Standard §8 PIDs only, plus optional unverified candidates with no formulas.
3. **Closed loop.** COS routes the JSON (Grok) and a CarAppService skeleton (Claude hand-off), logs both, and reports. No claim of an on-device build unless the user builds it.
4. **Routing load.** Process reads `cos/routing.yaml`.
5. **Bench.** Parked enumeration specialist; promote enhanced PIDs only with `bench-2019-4runner` evidence.
6. **Driving UI polish.** Only after a real projection session. Not part of bootstrap.

## 15. Glossary

Terms defined in META-COS §15 apply here (`COS`, `Car App Library`, `Hamilton Conversion Kit`, `OBDLink MX+`, `Event log`, `Hand-off prompt`, `Kotlin`).

- **Verified PID**: formula and request come from a named source and, for Toyota enhanced PIDs, from confirmation on this vehicle.
- **Unverified candidate**: a named signal we may want later. Not polled. Not shown as live data.
- **Driving set**: the five mode 01 PIDs in §8.

---

*— End of Project Specification —*
