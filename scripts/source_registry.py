#!/usr/bin/env python3
"""Shared registry helpers for the official Seoul/Gyeonggi collection network.

`sources.json` is the source of truth for the official network only. Private
sources are intentionally managed separately in `private_source_registry.py`.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES_PATH = ROOT / "sources.json"


def load_official_registry(path: Path | str = SOURCES_PATH) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data:
        raise ValueError("official source registry must be a non-empty object")
    return data


def official_registry_counts(path: Path | str = SOURCES_PATH) -> dict[str, dict[str, int]]:
    """Return central/support/total counts for every official registry group."""
    data = load_official_registry(path)
    out: dict[str, dict[str, int]] = {}
    for key, group in data.items():
        if not isinstance(group, dict):
            raise ValueError(f"invalid official source group: {key}")
        central = group.get("central")
        support = group.get("supportOffices") or []
        if central is not None and not isinstance(central, dict):
            raise ValueError(f"invalid central source definition: {key}")
        if not isinstance(support, list):
            raise ValueError(f"invalid supportOffices definition: {key}")
        central_count = 1 if central else 0
        support_count = len(support)
        out[str(key)] = {
            "central": central_count,
            "supportOffices": support_count,
            "total": central_count + support_count,
        }
    return out


def official_source_breakdown(path: Path | str = SOURCES_PATH) -> dict[str, int]:
    return {key: counts["total"] for key, counts in official_registry_counts(path).items()}


def official_support_office_counts(path: Path | str = SOURCES_PATH) -> dict[str, int]:
    return {key: counts["supportOffices"] for key, counts in official_registry_counts(path).items()}


def official_central_source_count(path: Path | str = SOURCES_PATH) -> int:
    return sum(counts["central"] for counts in official_registry_counts(path).values())


def official_source_count(path: Path | str = SOURCES_PATH) -> int:
    count = sum(official_source_breakdown(path).values())
    if count <= 0:
        raise ValueError("official source registry resolved to zero sources")
    return count


if __name__ == "__main__":
    print(official_source_count())
