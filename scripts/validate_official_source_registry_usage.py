\
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
