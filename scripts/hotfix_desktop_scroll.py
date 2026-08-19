#!/usr/bin/env python3
from pathlib import Path

p = Path(__file__).resolve().parents[1] / "index.html"
s = p.read_text(encoding="utf-8")
old = ".filter-panel{position:sticky;top:12px}"
new = ".filter-panel{position:sticky;top:12px}"
css = """
    @media(min-width:901px){
      html,body{height:100%;overflow:hidden}
      body{display:flex;flex-direction:column}
      .hero{flex:0 0 auto}
      main.wrap{flex:1 1 auto;min-height:0;width:100%;display:flex;flex-direction:column;overflow:hidden}
      .stats{flex:0 0 auto}
      .layout{flex:1 1 auto;min-height:0;margin-bottom:18px;align-items:stretch;overflow:hidden}
      .filter-panel{position:static;height:100%;max-height:none;overflow-y:auto;overscroll-behavior:contain;scrollbar-gutter:stable;padding-right:12px}
      .layout>section{min-height:0;height:100%;overflow-y:auto;overscroll-behavior:contain;scrollbar-gutter:stable;padding-right:4px}
      .footer{display:none}
    }
"""
if css.strip() not in s:
    marker = "    @media(max-width:900px){"
    if marker not in s:
        raise SystemExit("responsive CSS marker not found")
    s = s.replace(marker, css + marker, 1)
    p.write_text(s, encoding="utf-8")
    print("Applied independent desktop left/right scrolling")
else:
    print("Desktop independent scrolling already present")
