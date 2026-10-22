# Protocol folder summary

Updated 2026-10-22 after protocol JSON was written.

## Files present

- `protocol/toyota-4runner-2019.json` — schema `4runner-obd-protocol/1` for the 2019 Toyota 4Runner Limited 4WD. Contract is `specs/4runner-aa-obd.md` §9.

Scratch files `protocol/test.txt` and `protocol/overflow_test.py` are empty leftovers. They are not protocol definitions and must not be staged.

## What the JSON contains

- Five verified driving PIDs, all SAE J1979 mode 01: RPM `0C`, speed `0D`, coolant `05`, load `04`, throttle `11`.
- Three parked-only standard PIDs: intake `0F`, control-module voltage `42`, fuel level `2F`. Not in the driving poll group.
- No Toyota enhanced entries. A placeholder mode/PID would either duplicate the `(mode, pid, header)` triple or look like a live request. Candidates stay out until `bench-2019-4runner` supplies a real request.
- Adapter init array is empty until confirmed on hardware.
- `commands_forbidden` includes mode 08 and actuation.

## Status

Standard formulas only. No enhanced formula was invented. Next project step is `closed-loop` (CarAppService skeleton hand-off), not more PIDs.
