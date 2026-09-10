#!/usr/bin/env python3
"""Inventory GitHub Actions migration dependencies without reading secret values."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

OLD_IDENTITIES = ("puzzlex5", "puzzlex5.github.io")
SECRET_DOT_RE = re.compile(r"\bsecrets\.([A-Za-z_][A-Za-z0-9_]*)")
SECRET_INDEX_RE = re.compile(r"\bsecrets\[['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\]")
CRON_RE = re.compile(r"^\s*-\s*cron:\s*['\"]?([^'\"#\n]+)", re.MULTILINE)
SUSPICIOUS_SECRET_RE = re.compile(r"(?:PAT|TOKEN|SECRET|KEY|OAUTH|PASSWORD|PASSWD|CREDENTIAL|AUTH)", re.I)


def scan_workflow(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    secret_names = sorted(set(SECRET_DOT_RE.findall(text)) | set(SECRET_INDEX_RE.findall(text)))
    crons = [m.strip() for m in CRON_RE.findall(text)]
    old_refs = sorted({identity for identity in OLD_IDENTITIES if identity.lower() in text.lower()})
    external_or_personal = sorted(name for name in secret_names if name != "GITHUB_TOKEN")
    suspicious = sorted(name for name in external_or_personal if SUSPICIOUS_SECRET_RE.search(name))
    return {
        "path": str(path.as_posix()),
        "scheduled": bool(crons),
        "crons": crons,
        "secretRefs": secret_names,
        "externalSecretRefs": external_or_personal,
        "suspiciousCredentialRefs": suspicious,
        "oldIdentityRefs": old_refs,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--report", default="migration_independence_report.json")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    workflows_dir = root / ".github" / "workflows"
    rows = [scan_workflow(p) for p in sorted(workflows_dir.glob("*.y*ml"))]

    scheduled = [r for r in rows if r["scheduled"]]
    secret_rows = [r for r in rows if r["secretRefs"]]
    old_rows = [r for r in rows if r["oldIdentityRefs"]]
    all_external_secrets = sorted({s for r in rows for s in r["externalSecretRefs"]})
    all_suspicious = sorted({s for r in rows for s in r["suspiciousCredentialRefs"]})

    report = {
        "schemaVersion": 1,
        "workflowCount": len(rows),
        "scheduledWorkflowCount": len(scheduled),
        "scheduledWorkflows": [
            {"path": r["path"], "crons": r["crons"]} for r in scheduled
        ],
        "workflowSecretReferenceCount": len(secret_rows),
        "externalSecretNames": all_external_secrets,
        "suspiciousCredentialSecretNames": all_suspicious,
        "oldIdentityReferenceCount": len(old_rows),
        "oldIdentityReferences": [
            {"path": r["path"], "refs": r["oldIdentityRefs"]} for r in old_rows
        ],
        "workflows": rows,
        "healthy": not old_rows,
        "note": "Secret names are inventoried; secret values are never read or emitted. External secret ownership must be verified or rotated separately.",
    }

    out = Path(args.report)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({
        "workflowCount": report["workflowCount"],
        "scheduledWorkflowCount": report["scheduledWorkflowCount"],
        "scheduledWorkflows": report["scheduledWorkflows"],
        "externalSecretNames": report["externalSecretNames"],
        "suspiciousCredentialSecretNames": report["suspiciousCredentialSecretNames"],
        "oldIdentityReferenceCount": report["oldIdentityReferenceCount"],
        "healthy": report["healthy"],
    }, ensure_ascii=False, indent=2))

    return 0 if report["healthy"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
