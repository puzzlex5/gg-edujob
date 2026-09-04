#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

KST = timezone(timedelta(hours=9))
BASE = 'https://www.jobteacher.kr'
SURFACES = {
    'seoul': f'{BASE}/employ/lists/all/?is_search=yes&sel_area%5B%5D=1',
    'gyeonggi': f'{BASE}/employ/lists/all/?is_search=yes&sel_area%5B%5D=2422',
}


def main() -> int:
    samples = {}
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(locale='ko-KR')
        page = ctx.new_page()
        for surface, url in SURFACES.items():
            try:
                resp = page.goto(url, wait_until='domcontentloaded', timeout=45000)
                page.wait_for_timeout(800)
                if not resp or resp.status != 200:
                    raise RuntimeError(f'http-{resp.status if resp else 0}')
                soup = BeautifulSoup(page.content(), 'html.parser')
                rows = []
                for tr in soup.find_all('tr'):
                    anchors = []
                    for a in tr.find_all('a'):
                        href = a.get('href') or ''
                        if '/employ/detail/' in href:
                            anchors.append({'text': ' '.join(a.stripped_strings), 'href': urljoin(BASE, href), 'class': a.get('class') or []})
                    if not anchors:
                        continue
                    cells = []
                    for idx, td in enumerate(tr.find_all(['td','th'])):
                        cells.append({
                            'index': idx,
                            'text': ' '.join(td.stripped_strings),
                            'class': td.get('class') or [],
                            'anchors': [
                                {'text': ' '.join(a.stripped_strings), 'href': urljoin(BASE, a.get('href') or '')}
                                for a in td.find_all('a') if a.get('href')
                            ],
                        })
                    rows.append({'text': ' '.join(tr.stripped_strings), 'detailAnchors': anchors, 'cells': cells})
                    if len(rows) >= 25:
                        break
                samples[surface] = rows
            except Exception as e:
                errors.append({'surface': surface, 'error': f'{type(e).__name__}: {e}'})
        ctx.close(); browser.close()
    report = {
        'generatedAt': datetime.now(KST).isoformat(timespec='seconds'),
        'source': '잡티처',
        'probeOnly': True,
        'publicationEnabled': False,
        'samples': samples,
        'errors': errors,
        'safety': {'captchaBypass': False, 'tlsVerificationDisabled': False, 'authenticationBypass': False, 'writesToSource': False},
    }
    Path('jobteacher_row_schema_probe.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'errors': errors, 'sampleCounts': {k: len(v) for k, v in samples.items()}}, ensure_ascii=False))
    return 0 if not errors and all(samples.values()) else 2


if __name__ == '__main__':
    raise SystemExit(main())
