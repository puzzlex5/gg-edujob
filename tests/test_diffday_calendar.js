#!/usr/bin/env node
/**
 * diffDay() calendar-day regression test.
 *
 * Before this fix diffDay() compared a 23:59:59 deadline against today 00:00 and applied
 * Math.ceil(), so a posting closing TODAY reported D-1 and a posting that closed YESTERDAY
 * reported 0 (i.e. it was still treated as open and shown to users).
 *
 * Required semantics are pure calendar-day arithmetic:
 *   yesterday = -1 / today = 0 / tomorrow = 1 / +3 = 3 / +4 = 4 / +7 = 7 / +8 = 8 / null = null
 *
 * applyEnd source data is never modified; only the comparison is corrected.
 *
 * Run: node tests/test_diffday_calendar.js
 */
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');

// Pull the three real functions out of index.html so the test exercises shipped code.
function extract(name) {
  const i = html.indexOf('function ' + name + '(');
  if (i < 0) throw new Error('missing function ' + name);
  let depth = 0, started = false;
  for (let k = i; k < html.length; k++) {
    if (html[k] === '{') { depth++; started = true; }
    else if (html[k] === '}') { depth--; if (started && depth === 0) return html.slice(i, k + 1); }
  }
  throw new Error('unterminated ' + name);
}
const src = [extract('parseDate'), extract('today0'), extract('diffDay')].join('\n');
const diffDay = new Function(src + '\nreturn diffDay;')();

const pad = n => String(n).padStart(2, '0');
const offsetDate = n => {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() + n);
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())}`;
};

let pass = 0, fail = 0;
const check = (label, actual, expected) => {
  const ok = actual === expected;
  console.log(`${ok ? 'PASS ' : 'FAIL '} ${label}  expected=${expected} actual=${actual}`);
  ok ? pass++ : fail++;
};

// Required calendar-day boundaries
check('어제',        diffDay(offsetDate(-1)), -1);
check('오늘',        diffDay(offsetDate(0)),   0);
check('내일',        diffDay(offsetDate(1)),   1);
check('+3',          diffDay(offsetDate(3)),   3);
check('+4',          diffDay(offsetDate(4)),   4);
check('+7',          diffDay(offsetDate(7)),   7);
check('+8',          diffDay(offsetDate(8)),   8);
check('null(빈값)',  diffDay(''),           null);
check('null(undefined)', diffDay(undefined), null);
check('파싱불가',    diffDay('상시채용'),    null);

// Separator variants must behave identically (source feeds use both).
const t = new Date(); t.setHours(0, 0, 0, 0);
const y = t.getFullYear(), m = pad(t.getMonth() + 1), d = pad(t.getDate());
check('오늘 YYYY-MM-DD', diffDay(`${y}-${m}-${d}`), 0);
check('오늘 YYYY.MM.DD', diffDay(`${y}.${m}.${d}`), 0);

// The exclusion rule in filtered() is `d !== null && d < 0`; make sure the boundary is exact.
check('마감 제외 경계: 오늘은 제외되지 않음', diffDay(offsetDate(0)) < 0, false);
check('마감 제외 경계: 어제는 제외됨',       diffDay(offsetDate(-1)) < 0, true);

// ── 5-bucket comparator 경계 (main 구현을 그대로 추출해 검증) ─────────────────
// bucket 0: 0~3일 / bucket 1: 4~7일 / bucket 3: 8일 이상 / bucket 2: 마감없음+공식+최근7일
// bucket 4: 그 외 마감없음. diffDay 수정이 버킷 경계를 밀지 않는지 확인한다.
const bucketSrc = [extract('parseDate'), extract('today0'), extract('diffDay'),
                   extract('deadlineSortKey')].join('\n');
const deadlineSortKey = new Function(bucketSrc + '\nreturn deadlineSortKey;')();
const bucketOf = (endOffset, regOffset, feedKind) => deadlineSortKey({
  applyEnd: endOffset === null ? '' : offsetDate(endOffset),
  registered: regOffset === null ? '' : offsetDate(-regOffset),
  feedKind: feedKind || 'official',
  sourceIdentity: 'x'
})[0];

check('bucket 경계 D0  -> 0', bucketOf(0, 0), 0);
check('bucket 경계 D3  -> 0', bucketOf(3, 0), 0);
check('bucket 경계 D4  -> 1', bucketOf(4, 0), 1);
check('bucket 경계 D7  -> 1', bucketOf(7, 0), 1);
check('bucket 경계 D8  -> 3', bucketOf(8, 0), 3);
check('bucket 마감없음+공식+최근  -> 2', bucketOf(null, 3, 'official'), 2);
check('bucket 마감없음+공식+8일전 -> 4', bucketOf(null, 8, 'official'), 4);
check('bucket 마감없음+민간+최근  -> 4', bucketOf(null, 3, 'private'), 4);

// ── 데이터 의존 검사는 여기에 두지 않는다 ──────────────────────────────────
// 통합 데이터는 매시간 갱신되므로 건수/특정 날짜 분포를 영구 테스트에 고정하면
// 코드가 옳아도 곧 실패한다(예: 7,527 -> 7,526). 그런 값은 PR 시점의
// observational evidence 로만 기록한다.
// unified_jobs.json 이 이 PR 에서 변경되지 않았다는 사실은 파일을 다시 파싱하는
// 방식으로는 증명할 수 없으므로(같은 문자열을 두 번 읽는 동어반복),
// CI 에서 base..head 의 git diff 로 검증한다:
//   .github/workflows/diffday-hotfix-check.yml 의 'data files unchanged' 스텝

console.log(`\npassed=${pass} failed=${fail}`);
process.exit(fail ? 1 : 0);
