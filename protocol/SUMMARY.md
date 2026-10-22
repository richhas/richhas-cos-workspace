# Protocol folder summary

Updated 2026-10-22 after the project spec landed.

## Files present

None. Scratch files `protocol/test.txt` and `protocol/overflow_test.py` were removed before the GitHub checkpoint. They were not protocol definitions.

## Expected but missing

- `protocol/toyota-4runner-2019.json` — versioned protocol for the 2019 4Runner Android Auto app. Contract is `specs/4runner-aa-obd.md` §9. Not on disk yet.

## Status

`specs/4runner-aa-obd.md` exists and governs the JSON. Standard mode 01 PIDs only until a bench source exists for Toyota enhanced formulas. Next step is `protocol-json`, not another summary.
