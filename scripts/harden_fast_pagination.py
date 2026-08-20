#!/usr/bin/env python3
"""Normalize fast support-office pagination to one adaptive emergency ceiling.

Several older hotfixes may already have rewritten the original 7/9-page loops to a
literal 60-page loop.  This hardener must therefore accept either legacy form and
normalize both to FAST_MAX_PAGES.  It is deliberately idempotent so workflow
hardening cannot fail merely because an earlier compatibility patch already ran.
"""
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "scripts/scrape_jobs.py"
s = p.read_text(encoding="utf-8")

if "FAST_MAX_PAGES = 60" not in s:
    marker = 'BOARD_EXCLUDE = re.compile(r"구직|인력풀|합격|인사정보|인사발령|공지사항|자료실")\n'
    if marker not in s:
        raise SystemExit("Cannot locate collector constants marker")
    s = s.replace(marker, marker + "FAST_MAX_PAGES = 60  # emergency ceiling; normal stop is empty/repeated page\n", 1)

# Normalize every known historical loop form.  hotfix_support_office.py can run
# before this script and convert 7/9 to a literal 60, so literal 60 is not an error.
for old in (
    "for page in range(1, 8):",
    "for page in range(1, 10):",
    "for page in range(1, 61):",
):
    s = s.replace(old, "for page in range(1, FAST_MAX_PAGES + 1):")

# A page with no matching recent recruitment title is not proof that later rows
# are absent. Empty/repeated pages are the legitimate fast-stop signals.
s = s.replace("        if page_recent == 0 and page >= 2: break\n", "")
s = s.replace("        if page_recent==0 and page>=2: break\n", "")

# Gyeonggi metadata: record emergency-cap contact explicitly.
old_return = 'return out, {"rawRows":raw_rows,"explicitEmpty":explicit_empty,"pagesScanned":pages_scanned}'
new_return = 'return out, {"rawRows":raw_rows,"explicitEmpty":explicit_empty,"pagesScanned":pages_scanned,"capHit":pages_scanned >= FAST_MAX_PAGES}'
if old_return in s:
    s = s.replace(old_return, new_return, 1)
elif new_return not in s:
    raise SystemExit("Cannot locate Gyeonggi fast-board metadata return")

old_meta = '    explicit_empty = any(x["explicitEmpty"] for x in meta)\n'
new_meta = '    explicit_empty = any(x["explicitEmpty"] for x in meta)\n    cap_hit = any(x.get("capHit") for x in meta)\n'
if new_meta not in s:
    if old_meta not in s:
        raise SystemExit("Cannot locate Gyeonggi support-office metadata aggregation")
    s = s.replace(old_meta, new_meta, 1)

old_state = '    if not boards:\n        state, ok, msg = "error", False, "실제 채용 게시판을 찾지 못함"\n    elif raw_total > 0:\n'
new_state = '    if not boards:\n        state, ok, msg = "error", False, "실제 채용 게시판을 찾지 못함"\n    elif cap_hit:\n        state, ok, msg = "warning", False, "빠른 수집 비상 페이지 상한 도달 · 깊은 감사 필요"\n    elif raw_total > 0:\n'
if new_state not in s:
    if old_state not in s:
        raise SystemExit("Cannot locate Gyeonggi support-office health classification")
    s = s.replace(old_state, new_state, 1)

required = (
    "FAST_MAX_PAGES = 60",
    "range(1, FAST_MAX_PAGES + 1)",
    '"capHit":pages_scanned >= FAST_MAX_PAGES',
)
for marker in required:
    if marker not in s:
        raise SystemExit(f"Fast pagination hardening missing: {marker}")

# The old fixed support-office loops must not survive this phase.
for forbidden in ("range(1, 8)", "range(1, 10)", "range(1, 61)"):
    block = s[s.find("def scrape_mircms_board"):s.find("def dedupe_merge")]
    if forbidden in block:
        raise SystemExit(f"Fixed support-office page ceiling remains: {forbidden}")

p.write_text(s, encoding="utf-8")

# Stable posting IDs and conservative deduplication are part of the same mandatory
# pre-crawl hardening phase. Generic titles from different schools must not merge.
runpy.run_path(str(ROOT / "scripts/harden_identity_dedupe.py"), run_name="__main__")

print("Fast support-office pagination hardened")
