#!/usr/bin/env python3
from pathlib import Path

root = Path(__file__).resolve().parents[1]
index = (root / "index.html").read_text(encoding="utf-8")
mobile = (root / "mobile-ui.js").read_text(encoding="utf-8")

loads = index.count('src="mobile-ui.js?')
assert loads >= 1, "mobile-ui.js must be loaded"
assert "if(window.__edujobMobileUiLoaded)return;" in mobile, "duplicate mobile-ui loads must return before binding"
assert "window.__edujobMobileUiLoaded=true;" in mobile, "mobile UI must mark itself initialized"
assert mobile.index("if(window.__edujobMobileUiLoaded)return;") < mobile.index("btn.addEventListener('click'"), "idempotence guard must run before click binding"
assert mobile.count("btn.addEventListener('click'") == 1, "source must define one mobile filter click binding"

print(f"PASS mobile filter initialization is idempotent (index loads={loads})")
