#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date()
JOBS = Path('jobteacher_jobs.json')
RECON = Path('jobteacher_reconciliation_report.json')
DETAIL = Path('jobteacher_detail_link_report.json')
OUT = Path('jobteacher_publication_gate_audit.json')
MMDD_RE = re.compile(r'(?<!\d)(\d{1,2})[./-](\d{1,2})(?!\d)')
YYYYMMDD_RE = re.compile(r'(20\d{2})[./-](\d{1,2})[./-](\d{1,2})')
D_RE = re.compile(r'\bD\s*[-+]\s*(\d+)\b', re.I)
ALWAYS_RE = re.compile(r'상시|채용시|충원시|수시', re.I)
CLOSED_RE = re.compile(r'마감|종료|채용완료|접수완료', re.I)
BANNED_RE = re.compile(r'구직|학원\s*매매|악기\s*(?:판매|매매)|연습실|원생\s*모집|학생\s*모집|레슨생\s*모집|팝니다|삽니다|권리금|임대', re.I)


def load(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def end_semantics(text: str):
    s = str(text or '').strip()
    if not s:
        return 'missing', None
    if CLOSED_RE.search(s):
        return 'explicit_closed', None
    if ALWAYS_RE.search(s):
        return 'open_ended', None
    m = D_RE.search(s)
    if m:
        # D-0 is still active today; D+N means elapsed/closed semantics.
        sign_match = re.search(r'D\s*([+-])\s*(\d+)', s, re.I)
        if sign_match and sign_match.group(1) == '+':
            return 'past_d_day', None
        return 'active_d_day', None
    m = YYYYMMDD_RE.search(s)
    if m:
        try:
            d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=KST).date()
            return ('dated_past' if d < TODAY else 'dated_current'), d.isoformat()
        except ValueError:
            return 'unparsed', None
    m = MMDD_RE.search(s)
    if m:
        try:
            d = TODAY.replace(month=int(m.group(1)), day=int(m.group(2)))
            return ('mmdd_past' if d < TODAY else 'mmdd_current'), d.isoformat()
        except ValueError:
            return 'unparsed', None
    return 'unparsed', None


def main():
    jobs = load(JOBS, [])
    if isinstance(jobs, dict):
        jobs = jobs.get('jobs', [])
    recon = load(RECON, {})
    detail = load(DETAIL, {})

    deadline_buckets = Counter()
    examples = defaultdict(list)
    province_counts = Counter()
    missing_links = []
    bad_identity = []
    banned = []
    duplicate_ids = []
    seen = set()
    multi_province_rows = []
    raw_location_both = []

    for j in jobs:
        sid = str(j.get('sourceIdentity') or '')
        if sid in seen:
            duplicate_ids.append(sid)
        seen.add(sid)
        if not sid.startswith('jobteacher:'):
            bad_identity.append(sid)
        province = str(j.get('province') or '')
        province_counts[province] += 1
        provinces = [str(x) for x in (j.get('provinces') or []) if str(x)]
        if len(set(provinces)) > 1:
            multi_province_rows.append({'id': sid, 'provinces': provinces})
        blob = ' '.join(str(j.get(k) or '') for k in ('title','rawRowText','location','region'))
        if '서울' in blob and ('경기' in blob or '경기도' in blob):
            raw_location_both.append({'id': sid, 'province': province, 'text': blob[:600]})
        if BANNED_RE.search(str(j.get('title') or '')):
            banned.append({'id': sid, 'title': j.get('title')})
        if not (j.get('url') or j.get('originalUrl')):
            missing_links.append(sid)
        status, parsed = end_semantics(str(j.get('applyEnd') or j.get('applyEndText') or ''))
        deadline_buckets[status] += 1
        if len(examples[status]) < 15:
            examples[status].append({'id': sid, 'end': j.get('applyEnd') or j.get('applyEndText'), 'parsed': parsed, 'title': j.get('title'), 'province': province})

    past_like = deadline_buckets['explicit_closed'] + deadline_buckets['past_d_day'] + deadline_buckets['dated_past'] + deadline_buckets['mmdd_past']
    unknown_like = deadline_buckets['missing'] + deadline_buckets['unparsed']
    source_surface_total = sum(int(x.get('ids') or 0) for x in (recon.get('sourceReports') or []))
    cross_surface_overlap = max(0, source_surface_total - len(jobs))

    hard_gates = {
        'reconciliationHealthy': bool(recon.get('healthy')),
        'traversalComplete': bool(recon.get('traversalComplete')),
        'missingAfterZero': int(recon.get('missingAfterCount') or 0) == 0,
        'detailHealthy': bool(detail.get('healthy')),
        'detailCoverageComplete': bool(detail.get('detailCoverageComplete')),
        'detailErrorZero': int(detail.get('detailErrorCount') or 0) == 0,
        'allStableIdsValid': not bad_identity,
        'noDuplicateStableIds': not duplicate_ids,
        'noMissingLinks': not missing_links,
        'onlyMetroProvince': set(province_counts) <= {'서울','경기'},
        'noBannedTitles': not banned,
        'noProvablyExpiredRows': past_like == 0,
    }
    report = {
        'generatedAt': datetime.now(KST).isoformat(timespec='seconds'),
        'source': '잡티처',
        'policy': 'jobteacher-publication-gate-audit-v1',
        'candidateCount': len(jobs),
        'publicationEnabledNow': recon.get('publicationEnabled'),
        'hardGates': hard_gates,
        'hardGatePass': all(hard_gates.values()),
        'deadlineBuckets': dict(deadline_buckets),
        'provablyExpiredCount': past_like,
        'deadlineUnknownCount': unknown_like,
        'deadlineExamples': dict(examples),
        'provinceCounts': dict(province_counts),
        'sourceSurfaceIdOccurrences': source_surface_total,
        'crossSurfaceStableIdOverlap': cross_surface_overlap,
        'rowsWithExplicitMultipleProvinces': len(multi_province_rows),
        'rowsWhoseRawTextMentionsBothMetroProvinces': len(raw_location_both),
        'rawBothExamples': raw_location_both[:30],
        'badIdentityCount': len(bad_identity),
        'duplicateStableIdCount': len(duplicate_ids),
        'missingLinkCount': len(missing_links),
        'bannedTitleCount': len(banned),
        'recommendation': 'enable only if hardGatePass and current-state semantics are defensible; otherwise repair crawler before promotion',
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('candidateCount','hardGatePass','deadlineBuckets','provablyExpiredCount','deadlineUnknownCount','crossSurfaceStableIdOverlap','rowsWithExplicitMultipleProvinces','rowsWhoseRawTextMentionsBothMetroProvinces')}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
