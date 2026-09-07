#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from datetime import datetime, timezone, timedelta
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "verify_lessoninfo_culture_cold.py"
spec = importlib.util.spec_from_file_location("lessoninfo_cold", MODULE)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)

KST = timezone(timedelta(hours=9))


def test_known_bad_sentinel_is_permanent():
    assert "culture:id:94673" in mod.KNOWN_BAD_COLD_IDS


def test_exact_id_and_route_binding():
    target = "https://www.lessoninfo.co.kr/culture-jobs/detail.php?id=94680"
    assert mod.lessoninfo_route_matches(target, target, "94680")
    assert not mod.lessoninfo_route_matches(
        target, "https://www.lessoninfo.co.kr/culture-jobs/list.php", "94680"
    )
    assert not mod.lessoninfo_route_matches(
        target, "https://www.lessoninfo.co.kr/culture-jobs/detail.php?id=94681", "94680"
    )


def test_title_binding_rejects_unrelated_post():
    expected = "국립박물관문화재단 2026년 3차 직원 채용"
    assert mod.title_matches(expected, "국립박물관문화재단 2026년 3차 직원 채용 모집요강")
    assert not mod.title_matches(expected, "서울시립교향악단 바이올린 단원 공개모집")


def test_deadline_formats(monkeypatch):
    fixed = datetime(2026, 9, 7, 9, 0, tzinfo=KST)
    monkeypatch.setattr(mod, "now_kst", lambda: fixed)
    cases = [
        "공고 (~8/17)",
        "공고 (~8.17)",
        "공고 ~ 8.17 마감",
        "공고 8/17 마감",
        "공고 마감 8.17",
        "공고 8월 17일까지",
    ]
    for title in cases:
        assert mod.title_deadline(title, "2026-08-01") == "2026-08-17", title
