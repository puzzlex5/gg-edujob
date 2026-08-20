#!/usr/bin/env python3
from pathlib import Path

# UI safety guard used by the data-refresh workflow.
# Do not rewrite index.html here: manual UI improvements must not be reverted by an hourly data run.
p = Path(__file__).resolve().parents[1] / "index.html"
s = p.read_text(encoding="utf-8")

required = [
    "@media (hover:hover) and (pointer:fine)",
    "overflow-y:auto",
    "overscroll-behavior-y:contain",
    ".filter-panel",
    ".layout>section",
]
missing = [token for token in required if token not in s]
if missing:
    raise SystemExit("Desktop independent-scroll guard failed; missing: " + ", ".join(missing))

print("Desktop independent left/right scrolling is present; no UI rewrite needed")
