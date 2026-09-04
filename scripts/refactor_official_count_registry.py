#!/usr/bin/env python3
from __future__ import annotations

import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def write(rel: str, text: str) -> None:
    (ROOT / rel).write_text(text, encoding="utf-8")


def replace_once(rel: str, old: str, new: str) -> None:
    text = read(rel)
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{rel}: expected exactly one match, found {count}: {old[:120]!r}")
    write(rel, text.replace(old, new, 1))


def replace_all_exact(rel: str, old: str, new: str, expected: int) -> None:
    text = read(rel)
    count = text.count(old)
    if count != expected:
        raise SystemExit(f"{rel}: expected {expected} matches, found {count}: {old!r}")
    write(rel, text.replace(old, new))


# Python consumers: derive current official count from sources.json while preserving policy-version labels.
replace_once(
    "scripts/validate_reconciliation_policy.py",
    "import json\nfrom pathlib import Path\n",
    "import json\nfrom pathlib import Path\n\nfrom source_registry import official_source_count\n",
)
replace_once(
    "scripts/validate_reconciliation_policy.py",
    "def main():\n    report = load(\"source_reconciliation_report.json\")\n",
    "def main():\n    expected_sources = official_source_count()\n    report = load(\"source_reconciliation_report.json\")\n",
)
replace_once(
    "scripts/validate_reconciliation_policy.py",
    "    if int(summary.get(\"totalSources\") or 0) != 38 or int(summary.get(\"reconciledSources\") or 0) != 38:\n        raise SystemExit(\"Current-policy reconciliation is not 38/38\")\n",
    "    if (\n        int(summary.get(\"totalSources\") or 0) != expected_sources\n        or int(summary.get(\"reconciledSources\") or 0) != expected_sources\n    ):\n        raise SystemExit(\n            f\"Current-policy reconciliation is not {expected_sources}/{expected_sources}\"\n        )\n",
)

replace_once(
    "scripts/verify_jobs.py",
    "from urllib3.util.retry import Retry\n",
    "from urllib3.util.retry import Retry\n\nfrom source_registry import official_source_count\n",
)
replace_once(
    "scripts/verify_jobs.py",
    "    summary = report.get(\"summary\") or {}\n    if (\n        int(summary.get(\"totalSources\") or 0) != 38\n        or int(summary.get(\"reconciledSources\") or 0) != 38\n",
    "    summary = report.get(\"summary\") or {}\n    expected_sources = official_source_count()\n    if (\n        int(summary.get(\"totalSources\") or 0) != expected_sources\n        or int(summary.get(\"reconciledSources\") or 0) != expected_sources\n",
)

replace_once(
    "scripts/verify_support_completeness.py",
    "from urllib.parse import parse_qs, urlparse\n",
    "from urllib.parse import parse_qs, urlparse\n\nfrom source_registry import official_source_count\n",
)
replace_once(
    "scripts/verify_support_completeness.py",
    "    if int(summary.get(\"reconciledSources\") or 0) != 38 or int(summary.get(\"totalSources\") or 0) != 38:\n        return {}\n",
    "    expected_sources = official_source_count()\n    if (\n        int(summary.get(\"reconciledSources\") or 0) != expected_sources\n        or int(summary.get(\"totalSources\") or 0) != expected_sources\n    ):\n        return {}\n",
)

replace_once(
    "scripts/verify_central_reconciliation_consistency.py",
    "from pathlib import Path\n",
    "from pathlib import Path\n\nfrom source_registry import official_source_count\n",
)
replace_once(
    "scripts/verify_central_reconciliation_consistency.py",
    "    central = load(CENTRAL)\n    recon = load(RECON)\n    if not central.get(\"complete\"):\n        raise SystemExit(\"Central pagination report is not complete\")\n    if int((recon.get(\"summary\") or {}).get(\"reconciledSources\") or 0) != 38:\n        raise SystemExit(\"38-source reconciliation is not complete\")\n",
    "    central = load(CENTRAL)\n    recon = load(RECON)\n    expected_sources = official_source_count()\n    if not central.get(\"complete\"):\n        raise SystemExit(\"Central pagination report is not complete\")\n    summary = recon.get(\"summary\") or {}\n    if (\n        int(summary.get(\"reconciledSources\") or 0) != expected_sources\n        or int(summary.get(\"totalSources\") or 0) != expected_sources\n    ):\n        raise SystemExit(f\"{expected_sources}-source reconciliation is not complete\")\n",
)

replace_once(
    "scripts/update_collector_status.py",
    "from urllib.parse import parse_qs, urlparse\n",
    "from urllib.parse import parse_qs, urlparse\n\nfrom source_registry import official_source_count\n",
)
replace_once(
    "scripts/update_collector_status.py",
    "    report = load_json(ROOT / \"source_reconciliation_report.json\", {})\n    summary = report.get(\"summary\", {}) if isinstance(report, dict) else {}\n",
    "    report = load_json(ROOT / \"source_reconciliation_report.json\", {})\n    summary = report.get(\"summary\", {}) if isinstance(report, dict) else {}\n    expected_sources = official_source_count()\n",
)
replace_once(
    "scripts/update_collector_status.py",
    "        total == 38\n        and reconciled == 38\n",
    "        total == expected_sources\n        and reconciled == expected_sources\n",
)

# Workflow inline guards: resolve the same official registry at runtime.
replace_once(
    ".github/workflows/daily-deep-audit.yml",
    "          python - <<'PY'\n          import json\n          from pathlib import Path\n          before=json.loads(Path('/tmp/jobs_daily_baseline.json').read_text(encoding='utf-8')).get('jobs',[])\n",
    "          python - <<'PY'\n          import json\n          import sys\n          from pathlib import Path\n          sys.path.insert(0, 'scripts')\n          from source_registry import official_source_count\n          expected_sources=official_source_count()\n          before=json.loads(Path('/tmp/jobs_daily_baseline.json').read_text(encoding='utf-8')).get('jobs',[])\n",
)
replace_once(
    ".github/workflows/daily-deep-audit.yml",
    "          if total != 38 or reconciled != total:\n              raise SystemExit(f'Daily 38-source official-ID audit incomplete: {summary}')\n",
    "          if total != expected_sources or reconciled != total:\n              raise SystemExit(f'Daily {expected_sources}-source official-ID audit incomplete: {summary}')\n",
)
replace_once(
    ".github/workflows/daily-deep-audit.yml",
    "          if int(qsummary.get('missingOfficialIds') or 0) != 0 or int(qsummary.get('reconciledSources') or 0) != 38:\n",
    "          if int(qsummary.get('missingOfficialIds') or 0) != 0 or int(qsummary.get('reconciledSources') or 0) != expected_sources:\n",
)

replace_once(
    ".github/workflows/recover-missing-jobs.yml",
    "          python - <<'PY'\n          import json\n          from pathlib import Path\n          before=json.loads(Path('/tmp/jobs_recovery_base.json').read_text(encoding='utf-8')).get('jobs',[])\n",
    "          python - <<'PY'\n          import json\n          import sys\n          from pathlib import Path\n          sys.path.insert(0, 'scripts')\n          from source_registry import official_source_count\n          expected_sources=official_source_count()\n          before=json.loads(Path('/tmp/jobs_recovery_base.json').read_text(encoding='utf-8')).get('jobs',[])\n",
)
replace_once(
    ".github/workflows/recover-missing-jobs.yml",
    "          if total != 38 or reconciled != total:\n              raise SystemExit(f'Not all 38 official sources reconciled: {summary}')\n",
    "          if total != expected_sources or reconciled != total:\n              raise SystemExit(f'Not all {expected_sources} official sources reconciled: {summary}')\n",
)
replace_once(
    ".github/workflows/recover-missing-jobs.yml",
    "scripts/verify_jobs.py scripts/update_collection_state.py scripts/update_collector_status.py scripts/sync_effective_audit_status.py scripts/sanitize_job_dates.py scripts/harden_deep_audit.py",
    "scripts/verify_jobs.py scripts/update_collection_state.py scripts/update_collector_status.py scripts/sync_effective_audit_status.py scripts/sanitize_job_dates.py scripts/harden_deep_audit.py scripts/source_registry.py",
)

replace_once(
    ".github/workflows/source-id-reconciliation.yml",
    "  push:\n    paths:\n",
    "  push:\n    branches: [main]\n    paths:\n",
)
replace_once(
    ".github/workflows/source-id-reconciliation.yml",
    "      - 'scripts/validate_reconciliation_policy.py'\n",
    "      - 'scripts/validate_reconciliation_policy.py'\n      - 'scripts/source_registry.py'\n",
)
replace_once(
    ".github/workflows/source-id-reconciliation.yml",
    "scripts/update_collection_state.py scripts/update_collector_status.py scripts/sync_effective_audit_status.py",
    "scripts/update_collection_state.py scripts/update_collector_status.py scripts/sync_effective_audit_status.py scripts/source_registry.py",
)
replace_once(
    ".github/workflows/source-id-reconciliation.yml",
    "          python - <<'PY'\n          import json\n          from pathlib import Path\n          r=json.loads(Path('source_reconciliation_report.json').read_text(encoding='utf-8'))\n",
    "          python - <<'PY'\n          import json\n          import sys\n          from pathlib import Path\n          sys.path.insert(0, 'scripts')\n          from source_registry import official_source_count\n          expected_sources=official_source_count()\n          r=json.loads(Path('source_reconciliation_report.json').read_text(encoding='utf-8'))\n",
)
replace_once(
    ".github/workflows/source-id-reconciliation.yml",
    "          if total != 38 or reconciled != total:\n              raise SystemExit(f'38-source reconciliation incomplete: {s}')\n",
    "          if total != expected_sources or reconciled != total:\n              raise SystemExit(f'{expected_sources}-source reconciliation incomplete: {s}')\n",
)

# Permanent guard against re-introducing functional literal source-count checks.
validation_script = textwrap.dedent(r'''\
#!/usr/bin/env python3
"""Verify official-source count consumers all use sources.json as the single numeric authority."""
from __future__ import annotations

import json
import re
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from source_registry import official_source_count  # noqa: E402

PY_TARGETS = (
    "scripts/validate_reconciliation_policy.py",
    "scripts/verify_jobs.py",
    "scripts/verify_support_completeness.py",
    "scripts/verify_central_reconciliation_consistency.py",
    "scripts/update_collector_status.py",
)
WORKFLOW_TARGETS = (
    ".github/workflows/daily-deep-audit.yml",
    ".github/workflows/recover-missing-jobs.yml",
    ".github/workflows/source-id-reconciliation.yml",
)
FORBIDDEN_FUNCTIONAL = (
    re.compile(r"(?:totalSources|reconciledSources)[^\n]{0,120}(?:==|!=)\s*38"),
    re.compile(r"\b(?:total|reconciled)\s*(?:==|!=)\s*38\b"),
)


def compile_python_heredocs(path: Path) -> int:
    lines = path.read_text(encoding="utf-8").splitlines()
    compiled = 0
    i = 0
    while i < len(lines):
        if "python - <<'PY'" not in lines[i]:
            i += 1
            continue
        i += 1
        body = []
        while i < len(lines) and lines[i].strip() != "PY":
            body.append(lines[i])
            i += 1
        if i >= len(lines):
            raise SystemExit(f"unterminated Python heredoc: {path}")
        source = textwrap.dedent("\n".join(body)) + "\n"
        compile(source, f"{path}:heredoc:{compiled + 1}", "exec")
        compiled += 1
        i += 1
    return compiled


def main() -> None:
    expected = official_source_count()
    if expected <= 0:
        raise SystemExit("official source registry resolved to zero")

    policy_text = (ROOT / "scripts/validate_reconciliation_policy.py").read_text(encoding="utf-8")
    if 'POLICY = "stable-id-38-v2-gyeonggi-central-90d"' not in policy_text:
        raise SystemExit("policy version identifier changed unexpectedly; it is not a live source-count field")

    violations = []
    for rel in (*PY_TARGETS, *WORKFLOW_TARGETS):
        text = (ROOT / rel).read_text(encoding="utf-8")
        for pattern in FORBIDDEN_FUNCTIONAL:
            if pattern.search(text):
                violations.append(f"{rel}: {pattern.pattern}")
    if violations:
        raise SystemExit("functional literal official source count reintroduced: " + "; ".join(violations))

    compiled_heredocs = sum(compile_python_heredocs(ROOT / rel) for rel in WORKFLOW_TARGETS)

    report = json.loads((ROOT / "source_reconciliation_report.json").read_text(encoding="utf-8"))
    summary = report.get("summary") or {}
    total = int(summary.get("totalSources") or 0)
    reconciled = int(summary.get("reconciledSources") or 0)
    missing = int(summary.get("missingAfter") or 0)
    if total != expected or reconciled != expected or missing != 0:
        raise SystemExit(
            f"canonical reconciliation evidence disagrees with sources.json: "
            f"expected={expected}, total={total}, reconciled={reconciled}, missingAfter={missing}"
        )

    print(json.dumps({
        "officialSourceCount": expected,
        "reconciledSources": reconciled,
        "missingAfter": missing,
        "compiledWorkflowPythonBlocks": compiled_heredocs,
        "policyVersionPreserved": True,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
''')
write("scripts/validate_official_source_registry_usage.py", validation_script)

validation_workflow = textwrap.dedent(r'''\
name: Official source registry safety

on:
  pull_request:
    paths:
      - 'sources.json'
      - 'scripts/source_registry.py'
      - 'scripts/validate_official_source_registry_usage.py'
      - 'scripts/validate_reconciliation_policy.py'
      - 'scripts/verify_jobs.py'
      - 'scripts/verify_support_completeness.py'
      - 'scripts/verify_central_reconciliation_consistency.py'
      - 'scripts/update_collector_status.py'
      - '.github/workflows/daily-deep-audit.yml'
      - '.github/workflows/recover-missing-jobs.yml'
      - '.github/workflows/source-id-reconciliation.yml'
      - '.github/workflows/official-registry-safety.yml'
  push:
    branches: [main]
    paths:
      - 'sources.json'
      - 'scripts/source_registry.py'
      - 'scripts/validate_official_source_registry_usage.py'
      - 'scripts/validate_reconciliation_policy.py'
      - 'scripts/verify_jobs.py'
      - 'scripts/verify_support_completeness.py'
      - 'scripts/verify_central_reconciliation_consistency.py'
      - 'scripts/update_collector_status.py'
      - '.github/workflows/daily-deep-audit.yml'
      - '.github/workflows/recover-missing-jobs.yml'
      - '.github/workflows/source-id-reconciliation.yml'
      - '.github/workflows/official-registry-safety.yml'

permissions:
  contents: read

concurrency:
  group: official-registry-safety-${{ github.event_name == 'pull_request' && github.event.pull_request.number || github.ref }}
  cancel-in-progress: true

jobs:
  validate:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v7
        with:
          python-version: '3.12'
      - name: Compile official count consumers
        run: |
          python -m py_compile \
            scripts/source_registry.py \
            scripts/validate_official_source_registry_usage.py \
            scripts/validate_reconciliation_policy.py \
            scripts/verify_jobs.py \
            scripts/verify_support_completeness.py \
            scripts/verify_central_reconciliation_consistency.py \
            scripts/update_collector_status.py
      - name: Verify registry-derived official completeness invariants
        run: python scripts/validate_official_source_registry_usage.py
      - name: Re-run current reconciliation policy gate
        run: python scripts/validate_reconciliation_policy.py
''')
write(".github/workflows/official-registry-safety.yml", validation_workflow)

print("official source-count registry refactor applied")
