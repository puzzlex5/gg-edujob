#!/usr/bin/env python3
"""Idempotently add standard resilient HTTP retry/backoff to crawler sessions."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    "scripts/scrape_jobs.py",
    "scripts/repair_gyeonggi_support.py",
    "scripts/complete_support_coverage.py",
    "scripts/resolve_support_links.py",
    "scripts/enrich_support_periods.py",
    "scripts/enrich_seoul_get_fallback.py",
]
IMPORTS = "from requests.adapters import HTTPAdapter\nfrom urllib3.util.retry import Retry\n"
POLICY = '''\nRETRY_POLICY = Retry(\n    total=3, connect=3, read=3, status=3, backoff_factor=0.8,\n    status_forcelist=(408, 429, 500, 502, 503, 504),\n    allowed_methods=frozenset(("GET", "POST")),\n    respect_retry_after_header=True, raise_on_status=False,\n)\nS.mount("https://", HTTPAdapter(max_retries=RETRY_POLICY))\nS.mount("http://", HTTPAdapter(max_retries=RETRY_POLICY))\n'''

for rel in FILES:
    p = ROOT / rel
    if not p.exists():
        continue
    s = p.read_text(encoding="utf-8")
    if "from requests.adapters import HTTPAdapter" not in s:
        s = s.replace("import requests\n", "import requests\n" + IMPORTS, 1)
    if "RETRY_POLICY = Retry(" not in s:
        m = re.search(r"S\.headers\.update\(\{.*?\}\)\n", s, re.S)
        if not m:
            raise SystemExit(f"Cannot locate requests session header setup in {rel}")
        s = s[:m.end()] + POLICY + s[m.end():]
    p.write_text(s, encoding="utf-8")
    print("retry policy ready", rel)
