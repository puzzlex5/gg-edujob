#!/usr/bin/env python3
"""Remove arbitrary 7/9-page truncation from the fast support-office collectors.

Fast refreshes are allowed an emergency ceiling, but must normally stop because the
board ended/repeated. They must not stop merely because a page happened to contain
no matching recent recruitment titles.
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

s = s.replace("for page in range(1, 8):", "for page in range(1, FAST_MAX_PAGES + 1):", 1)
s = s.replace("for page in range(1, 10):", "for page in range(1, FAST_MAX_PAGES + 1):", 1)

# A page with no matching recruitment title is not proof that later rows are absent.
# Empty/repeated pages are already detected through page_candidates/page_raw == 0.
s = s.replace("        if page_recent == 0 and page >= 2: break\n", "", 1)
s = s.replace("        if page_recent==0 and page>=2: break\n", "", 1)

for old, new in [
    ('return out, {"rawRows":raw_rows,"explicitEmpty":explicit_empty,"pagesScanned":pages_scanned}',
     'return out, {"rawRows":raw_rows,"explicitEmpty":explicit_empty,"pagesScanned":pages_scanned,"capHit":pages_scanned >= FAST_MAX_PAGES}'),
    ('    explicit_empty = any(x["explicitEmpty"] for x in meta)\n',
     '    explicit_empty = any(x["explicitEmpty"] for x in meta)\n    cap_hit = any(x.get("capHit") for x in meta)\n'),
    ('    if not boards:\n        state, ok, msg = "error", False, "실제 채용 게시판을 찾지 못함"\n    elif raw_total > 0:\n',
     '    if not boards:\n        state, ok, msg = "error", False, "실제 채용 게시판을 찾지 못함"\n    elif cap_hit:\n        state, ok, msg = "warning", False, "빠른 수집 비상 페이지 상한 도달 · 깊은 감사 필요"\n    elif raw_total > 0:\n')
]:
    if old in s:
        s = s.replace(old, new, 1)
    elif new not in s:
        raise SystemExit(f"Unexpected fast collector source near: {old[:80]}")

for required in ("FAST_MAX_PAGES = 60", "range(1, FAST_MAX_PAGES + 1)", '"capHit":pages_scanned >= FAST_MAX_PAGES'):
    if required not in s:
        raise SystemExit(f"Fast pagination hardening missing: {required}")

p.write_text(s, encoding="utf-8")

# Apply stable posting IDs and conservative deduplication in the same mandatory
# pre-crawl hardening phase.  This prevents generic titles used by different
# schools from being silently collapsed into one job.
runpy.run_path(str(ROOT / "scripts/harden_identity_dedupe.py"), run_name="__main__")

print("Fast support-office pagination hardened")
