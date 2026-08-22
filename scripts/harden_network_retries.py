#!/usr/bin/env python3
"""Idempotently add standard resilient HTTP retry/backoff to crawler and verifier sessions."""
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
VERIFIER_POLICY = '''\n    retry_policy = Retry(\n        total=3, connect=3, read=3, status=3, backoff_factor=0.8,\n        status_forcelist=(408, 429, 500, 502, 503, 504),\n        allowed_methods=frozenset(("GET", "POST")),\n        respect_retry_after_header=True, raise_on_status=False,\n    )\n    session.mount("https://", HTTPAdapter(max_retries=retry_policy))\n    session.mount("http://", HTTPAdapter(max_retries=retry_policy))\n'''

patched = 0
skipped = 0
for rel in FILES:
    p = ROOT / rel
    if not p.exists():
        continue
    s = p.read_text(encoding="utf-8")
    # Some enrichers use thread-local or per-request sessions. Do not rewrite those blindly.
    if "S = requests.Session()" not in s or "S.headers.update" not in s:
        print("retry policy skipped (custom/no shared session)", rel)
        skipped += 1
        continue
    if "from requests.adapters import HTTPAdapter" not in s:
        if "import requests\n" not in s:
            raise SystemExit(f"requests import marker missing in {rel}")
        s = s.replace("import requests\n", "import requests\n" + IMPORTS, 1)
    if "RETRY_POLICY = Retry(" not in s:
        m = re.search(r"S\.headers\.update\(\{.*?\}\)\n", s, re.S)
        if not m:
            raise SystemExit(f"Cannot locate shared session header setup in {rel}")
        s = s[:m.end()] + POLICY + s[m.end():]
    p.write_text(s, encoding="utf-8")
    patched += 1
    print("retry policy ready", rel)

# The publication verifier intentionally rejects a fresh dataset when even one sampled
# support-office detail page is dead or mismatched. Give that live HTTP check the same
# retry/backoff protection as the collectors so a momentary timeout/5xx is not mistaken
# for a broken exact-link invariant.
verify = ROOT / "scripts/verify_jobs.py"
if verify.exists():
    s = verify.read_text(encoding="utf-8")
    if "from requests.adapters import HTTPAdapter" not in s:
        if "import requests\n" not in s:
            raise SystemExit("requests import marker missing in scripts/verify_jobs.py")
        s = s.replace("import requests\n", "import requests\n" + IMPORTS, 1)
    marker = "    session = requests.Session()\n"
    if "session.mount(\"https://\", HTTPAdapter(max_retries=retry_policy))" not in s:
        if marker not in s:
            raise SystemExit("Verifier session marker missing in scripts/verify_jobs.py")
        s = s.replace(marker, marker + VERIFIER_POLICY, 1)
    verify.write_text(s, encoding="utf-8")
    patched += 1
    print("retry policy ready scripts/verify_jobs.py")

if patched == 0:
    raise SystemExit("No crawler/verifier sessions were hardened; expected at least one")
print(f"network retry hardening complete: patched={patched}, skipped={skipped}")
