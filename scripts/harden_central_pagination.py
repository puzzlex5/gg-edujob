#!/usr/bin/env python3
"""Make both central recruitment portal crawlers stop on evidence, not fixed page counts.

The support-office collectors already use structural/retention-boundary termination. The two
central collectors still had hard-coded 34/59-page loops. This patch is deliberately idempotent:
it installs a large emergency ceiling while preserving the existing natural stops (empty page,
repeated IDs/no newly-added rows, and a short final Gyeonggi page).
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "scripts" / "scrape_jobs.py"


def main():
    text = TARGET.read_text(encoding="utf-8")

    if "CENTRAL_MAX_PAGES = 500" not in text:
        marker = "FAST_MAX_PAGES = 60  # emergency ceiling; normal stop is empty/repeated page\n"
        if marker not in text:
            raise SystemExit("FAST_MAX_PAGES marker not found; inspect scrape_jobs.py before patching")
        text = text.replace(
            marker,
            marker + "CENTRAL_MAX_PAGES = 500  # emergency ceiling; central crawlers normally stop on empty/repeated/final page\n",
            1,
        )

    # Patch only the central functions. Accept both historical fixed ranges and an already-patched form.
    gg_start = text.find("def scrape_gyeonggi_central():")
    gg_end = text.find("# -------------------- 경기 교육지원청", gg_start)
    se_start = text.find("def scrape_seoul_central():")
    se_end = text.find("# -------------------- 서울 11개 지원청", se_start)
    if min(gg_start, gg_end, se_start, se_end) < 0:
        raise SystemExit("Central collector function boundaries not found")

    gg = text[gg_start:gg_end]
    gg = re.sub(r"for page in range\(1,\s*(?:35|CENTRAL_MAX_PAGES\s*\+\s*1)\):",
                "for page in range(1, CENTRAL_MAX_PAGES + 1):", gg, count=1)
    se = text[se_start:se_end]
    se = re.sub(r"for page in range\(1,\s*(?:60|CENTRAL_MAX_PAGES\s*\+\s*1)\):",
                "for page in range(1, CENTRAL_MAX_PAGES + 1):", se, count=1)

    if "for page in range(1, CENTRAL_MAX_PAGES + 1):" not in gg:
        raise SystemExit("Gyeonggi central dynamic pagination patch did not apply")
    if "for page in range(1, CENTRAL_MAX_PAGES + 1):" not in se:
        raise SystemExit("Seoul central dynamic pagination patch did not apply")
    if "for page in range(1, 35):" in gg or "for page in range(1, 60):" in se:
        raise SystemExit("Fixed central page cap remains")

    text = text[:gg_start] + gg + text[gg_end:se_start] + se + text[se_end:]
    TARGET.write_text(text, encoding="utf-8")
    print("Central portal pagination hardened: evidence-based stop with 500-page emergency ceiling")


if __name__ == "__main__":
    main()
