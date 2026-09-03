#!/usr/bin/env python3
"""Keep the static GitHub Pages UI responsive as the recruitment dataset grows.

The collector intentionally keeps a large durable dataset for completeness. The browser must not
turn every matching row into DOM on each keystroke. This patch keeps full search/filter semantics,
renders results incrementally, lets normal HTTP cache revalidation work, and refreshes the small
UI helper asset when navigation/source-status behavior changes.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "index.html"
text = PATH.read_text(encoding="utf-8")

old_render = "function selectionSummary(){const parts=[];if(state.provinces.size)parts.push(`시·도 ${[...state.provinces].join(', ')}`);if(state.regions.size)parts.push(`지역 ${state.regions.size}곳`);if(state.schools.size)parts.push(`학교급 ${[...state.schools].join(', ')}`);if(state.types.size)parts.push(`직종 ${[...state.types].join(', ')}`);const box=$('#selectedBox');if(parts.length){box.className='selected-box show';box.innerHTML='<b>선택된 조건</b><br>'+parts.map(esc).join(' · ')}else{box.className='selected-box';box.textContent=''}}function render(){const a=filtered();$('#resultCount').textContent=a.length;$('#list').innerHTML=a.length?a.map(card).join(''):'<div class=\"empty\"><strong>조건에 맞는 모집 중 공고가 없습니다.</strong>체크한 지역·학교급·직종을 일부 해제하거나 검색어를 바꿔보세요.</div>';selectionSummary()}function renderAll(rebuild=true){if(rebuild)renderChecks();render()}"

new_render = "function selectionSummary(){const parts=[];if(state.provinces.size)parts.push(`시·도 ${[...state.provinces].join(', ')}`);if(state.regions.size)parts.push(`지역 ${state.regions.size}곳`);if(state.schools.size)parts.push(`학교급 ${[...state.schools].join(', ')}`);if(state.types.size)parts.push(`직종 ${[...state.types].join(', ')}`);const box=$('#selectedBox');if(parts.length){box.className='selected-box show';box.innerHTML='<b>선택된 조건</b><br>'+parts.map(esc).join(' · ')}else{box.className='selected-box';box.textContent=''}}let visibleLimit=80;const PAGE_SIZE=80;function render(resetLimit=true){if(resetLimit)visibleLimit=PAGE_SIZE;const a=filtered();$('#resultCount').textContent=a.length;if(!a.length){$('#list').innerHTML='<div class=\"empty\"><strong>조건에 맞는 모집 중 공고가 없습니다.</strong>체크한 지역·학교급·직종을 일부 해제하거나 검색어를 바꿔보세요.</div>';selectionSummary();return}const shown=a.slice(0,visibleLimit);const more=a.length>shown.length?`<button id=\"loadMoreJobs\" type=\"button\" style=\"width:100%;border:1px solid #dfe5ec;background:#fff;border-radius:12px;padding:13px;font-weight:800;cursor:pointer\">공고 더 보기 (${shown.length.toLocaleString()} / ${a.length.toLocaleString()})</button>`:'';$('#list').innerHTML=shown.map(card).join('')+more;const btn=$('#loadMoreJobs');if(btn)btn.addEventListener('click',()=>{visibleLimit+=PAGE_SIZE;render(false)});selectionSummary()}function renderAll(rebuild=true){if(rebuild)renderChecks();render()}"

if old_render in text:
    text = text.replace(old_render, new_render)
elif "let visibleLimit=80;const PAGE_SIZE=80;" not in text:
    raise SystemExit("render() signature changed; refusing unsafe performance patch")

old_load = "fetch('jobs.json?v='+Date.now(),{cache:'no-store'})"
new_load = "fetch('jobs.json',{cache:'no-cache'})"
if old_load in text:
    text = text.replace(old_load, new_load)
elif new_load not in text:
    raise SystemExit("jobs.json fetch pattern changed; refusing unsafe cache patch")

old_input = "$('#q').addEventListener('input',e=>{state.q=e.target.value;render()})"
new_input = "let searchTimer=0;$('#q').addEventListener('input',e=>{clearTimeout(searchTimer);const value=e.target.value;searchTimer=setTimeout(()=>{state.q=value;render()},180)})"
if old_input in text:
    text = text.replace(old_input, new_input)
elif "let searchTimer=0;" not in text:
    raise SystemExit("search input handler changed; refusing unsafe debounce patch")

# mobile-ui.js contains the visible official/private tabs and the Lessoninfo source-status panel.
# Bump only this asset reference so browsers do not keep the pre-Lessoninfo white-on-white version.
new_asset = "mobile-ui.js?v=20260903b"
if new_asset not in text:
    patched, count = re.subn(r"mobile-ui\.js\?v=[A-Za-z0-9._-]+", new_asset, text, count=1)
    if count != 1:
        raise SystemExit("mobile-ui.js asset reference changed; refusing unsafe cache-bust patch")
    text = patched

PATH.write_text(text, encoding="utf-8")
print("frontend hardening applied: incremental DOM, debounced search, cache revalidation, refreshed Lessoninfo UI asset")
