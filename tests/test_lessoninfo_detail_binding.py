#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_lessoninfo_browser_resilient as r


def check(label: str, ok: bool) -> None:
    print(("PASS  " if ok else "FAIL  ") + label)
    if not ok:
        raise AssertionError(label)


def main() -> int:
    check(
        "title deadline (~8.21.) is parsed from registered year",
        r._title_deadline("2026 광명문화재단 꿈의 무용단 감독 모집(~8.21.)", "2026-08-18")
        == "2026-08-21",
    )
    check(
        "ordinary title date is not promoted to deadline",
        r._title_deadline("2026년 제3차 직원 채용 8.20 등록", "2026-08-20") == "",
    )

    culture = {
        "sourceSurface": "culture-arts",
        "url": "https://www.lessoninfo.co.kr/culture-jobs/detail.php?id=94673",
    }
    check(
        "culture exact detail identity accepts harmless ckattempt",
        r._detail_route_matches(
            culture,
            "https://www.lessoninfo.co.kr/culture-jobs/detail.php?id=94673&ckattempt=1",
        ),
    )
    check(
        "culture redirect to list fails closed",
        not r._detail_route_matches(culture, "https://www.lessoninfo.co.kr/culture-jobs/list.php"),
    )
    check(
        "culture wrong individual id fails closed",
        not r._detail_route_matches(
            culture, "https://www.lessoninfo.co.kr/culture-jobs/detail.php?id=94674"
        ),
    )

    after = {
        "sourceSurface": "afterschool-nulbom",
        "url": (
            "https://www.lessoninfo.co.kr/board/board.php?"
            "bo_table=20170316171033_6494&board_code=20130529164321_7227&code=&wr_no=7584"
        ),
    }
    check(
        "afterschool wr_no identity accepts harmless ckattempt",
        r._detail_route_matches(
            after,
            "https://www.lessoninfo.co.kr/board/board.php?"
            "bo_table=20170316171033_6494&board_code=20130529164321_7227&code=&wr_no=7584&ckattempt=1",
        ),
    )
    check(
        "afterschool wrong wr_no fails closed",
        not r._detail_route_matches(
            after,
            "https://www.lessoninfo.co.kr/board/board.php?"
            "bo_table=20170316171033_6494&board_code=20130529164321_7227&wr_no=7585",
        ),
    )

    item = {"titleHint": "(복사) 2026 광명문화재단 꿈의 무용단 감독 모집(~8.21.)"}
    check(
        "detail content binding tolerates list-only copy marker",
        r._detail_content_matches(
            item,
            "문화ㆍ예술 채용공고 2026 광명문화재단 꿈의 무용단 감독 모집(~8.21.) 상세 내용",
        ),
    )
    check(
        "wrong detail content fails closed",
        not r._detail_content_matches(item, "전혀 다른 기관의 채용 공고"),
    )

    fallback_item = {
        "sourceIdentity": "culture:id:94879",
        "sourceSurface": "culture-arts",
        "sourceSurfaceLabel": "문화예술 채용",
        "url": "https://www.lessoninfo.co.kr/culture-jobs/detail.php?id=94879",
        "titleHint": "[광림아트센터] BBCH홀 / 장천홀 공연장안내원 모집공고",
        "rowText": "서울 강남구 [광림아트센터] BBCH홀 / 장천홀 공연장안내원 모집공고 09.05 광림아트센터",
        "registeredHint": "2026-09-05",
    }
    fallback = r._culture_list_fallback(fallback_item, "detail-route-mismatch")
    check(
        "fresh metro culture row survives an unverified detail route",
        bool(fallback)
        and fallback.get("sourceIdentity") == "culture:id:94879"
        and fallback.get("metroRegion") == "서울",
    )
    check(
        "list-only culture fallback cannot expose the unverified individual link",
        bool(fallback)
        and fallback.get("url") == ""
        and fallback.get("originalUrl") == ""
        and fallback.get("detailLinkVerified") is False
        and fallback.get("unverifiedDetailUrl", "").endswith("id=94879"),
    )

    nonmetro = dict(fallback_item)
    nonmetro["sourceIdentity"] = "culture:id:94880"
    nonmetro["rowText"] = "부산 해운대구 공연장 안내원 모집 09.05 기관"
    check(
        "list-only fallback still rejects non-metro culture rows",
        r._culture_list_fallback(nonmetro, "detail-route-mismatch") is None,
    )

    unstable = dict(fallback_item)
    unstable["sourceIdentity"] = "culture:url:deadbeef"
    check(
        "list-only fallback requires a numeric stable culture identity",
        r._culture_list_fallback(unstable, "detail-route-mismatch") is None,
    )

    print("passed=13 failed=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
