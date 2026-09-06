#!/usr/bin/env python3
from pathlib import Path

p = Path(__file__).resolve().parents[1] / "index.html"
s = p.read_text(encoding="utf-8")

old_norm = "const $=s=>document.querySelector(s);const norm=s=>(s||'').toString().toLowerCase().replace(/\\s+/g,' ').trim();"
new_norm = "const $=s=>document.querySelector(s);const norm=s=>(s||'').toString().toLowerCase().normalize('NFKC').replace(/[\\u200b-\\u200d\\ufeff]/g,'').replace(/[^0-9a-z가-힣]+/gi,' ').replace(/\\s+/g,' ').trim();const searchTokens=q=>norm(q).split(' ').filter(Boolean);const searchHay=j=>norm([j.school,j.title,j.subject,j.region,(j.regions||[]).join(' '),j.type,j.source,j.schoolLevel,j.province].join(' '));const matchesQuery=(j,q)=>{const ts=searchTokens(q);if(!ts.length)return true;const hay=searchHay(j);return ts.every(t=>hay.includes(t))};"

old_search = "if(state.q){const hay=norm([j.school,j.title,j.subject,j.region,(j.regions||[]).join(' '),j.type,j.source].join(' '));if(!hay.includes(norm(state.q)))return false}"
new_search = "if(state.q&&!matchesQuery(j,state.q))return false;"

s = s.replace("if(state.q&&!matchesQuery(j,state.q))return falsereturn true", "if(state.q&&!matchesQuery(j,state.q))return false;return true")

if old_norm in s:
    s = s.replace(old_norm, new_norm, 1)
elif "const searchTokens=q=>" not in s:
    raise SystemExit("search normalizer marker not found")

if old_search in s:
    s = s.replace(old_search, new_search, 1)
elif "if(state.q&&!matchesQuery(j,state.q))return false;" not in s:
    raise SystemExit("search filter marker not found")

s = s.replace("최근 자동 갱신: '+data.updatedAt+' · 매일 오전 7시대 자동 갱신", "최근 자동 갱신: '+data.updatedAt+' · 매시간 자동 갱신")

if "direct-link-guard.js?v=20260906a" in s:
    s = s.replace("direct-link-guard.js?v=20260906a", "direct-link-guard.js?v=20260906c", 1)
elif "direct-link-guard.js?v=20260906c" not in s:
    raise SystemExit("direct-link-guard asset marker not found")

if "falsereturn" in s or "trueraise" in s:
    raise SystemExit("Refusing to write malformed JavaScript")

p.write_text(s, encoding="utf-8")
print("Robust multi-token search logic is present")
