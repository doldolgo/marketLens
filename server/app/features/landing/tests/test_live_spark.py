"""live 계산이 표 글자에서 spark 를 걷어 내고 파싱해도 결과가 원문 파싱과 같은지 (스펙 022 §3.2 live).

기준 = 원문 전체를 파싱한 결과다. 같은 표를 공백 있는 JSON(`json.dumps` 기본 구분자)으로 적으면 걷기 표식이
맞지 않아 원문을 그대로 파싱하므로, 그 결과를 기준으로 삼고 손으로 센 기대값도 함께 본다. 표 모양이 017 게시기와
다른 경우(spark 가 행 끝·다음 키가 status 가 아님·한 행만 다름·목록 안에 `[`)는 원문 파싱으로 되돌아가야 하고,
특히 한 행만 다를 때 두 행이 한 행으로 합쳐지면 안 된다.
"""

import json

from app.features.landing.service import _strip_spark, build_live
from app.features.landing.tests.helpers import row

SPARK = [0.512, -0.25, 1.0, 1.25e-05, -0.0]


def _payload(rows: list[dict]) -> dict:
    return {
        "rate": 1360.0,
        "notional": 1000.0,
        "rows": rows,
        "warnings": [],
        "dataReceivedAt": 1_790_509_107_000,
        "fetchedAt": 1_790_509_107_142,
    }


def _compact(rows: list[dict]) -> str:
    """017 게시기와 같은 공백 없는 표 글자."""
    return json.dumps(
        _payload(rows), ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )


def _reference(text: str) -> dict | None:
    """기준 — 공백 있는 JSON 으로 다시 적어 걷기 없이 원문 전체를 파싱한 live."""
    spaced = json.dumps(json.loads(text), ensure_ascii=False)
    assert _strip_spark(spaced) is spaced  # 표식이 맞지 않아 걷지 않는다
    live = build_live(spaced)
    return None if live is None else live.model_dump()


def _live(text: str) -> dict | None:
    live = build_live(text)
    return None if live is None else live.model_dump()


def _top(live: dict | None) -> list[tuple[str, str, float]]:
    assert live is not None
    return [(r["sym"], r["dir"], r["pct"]) for r in live["top"]]


def _rows() -> list[dict]:
    rows = [
        row("AAA", fwd=2.0, rev=-1.0, slip_fwd=0.3),
        row(
            "BBB", fwd=-0.5, rev=1.5, wd_dom=None
        ),  # 역프 쪽 국내 출금 모름 — 김프만 후보
        row(
            "CCC", fwd=3.0, rev=0.2, status="stale"
        ),  # 지금 값이 아니다 — 후보·over1 밖
        row("DDD", fwd=0.9, rev=1.1, dep_fx=False),
    ]
    for r in rows:
        r["spark"] = list(SPARK)
    return rows


# 위 네 행에서 손으로 센 값 — 코인당 하나, 값 내림차순
TOP = [("AAA", "kimp", 2.0), ("DDD", "kimp", 0.9), ("BBB", "kimp", -0.5)]


def _reorder(r: dict, order: list[str]) -> dict:
    return {k: r[k] for k in order}


def test_publisher_shaped_table_takes_the_strip_path_and_matches_full_parse() -> None:
    text = _compact(_rows())
    stripped = _strip_spark(text)
    assert '"spark"' not in stripped and len(stripped) < len(text)
    # 걷은 글자를 파싱하면 spark 키만 빠지고 나머지 값은 원문 파싱과 같다
    full = json.loads(text)
    for r in full["rows"]:
        del r["spark"]
    assert json.loads(stripped) == full
    live = _live(text)
    assert live == _reference(text)
    assert _top(live) == TOP
    # over1 = 원값 1.0 이상인 방향 — AAA 김프 2.3, BBB 역프 1.54, DDD 역프 1.14 (CCC 는 stale)
    assert (live["pairs"], live["coins"], live["over1"], live["over1_movable"]) == (
        4,
        4,
        3,
        1,
    )


def test_spark_as_the_last_key_falls_back_to_full_parse() -> None:
    rows = [_reorder(r, [k for k in r if k != "spark"] + ["spark"]) for r in _rows()]
    text = _compact(rows)
    assert _strip_spark(text) is text
    assert _live(text) == _reference(text)
    assert _top(_live(text)) == TOP


def test_another_key_between_spark_and_status_falls_back_to_full_parse() -> None:
    keys = list(_rows()[0])
    keys.remove("age")
    order = keys[: keys.index("spark") + 1] + ["age"] + keys[keys.index("spark") + 1 :]
    text = _compact([_reorder(r, order) for r in _rows()])
    assert _strip_spark(text) is text
    assert _live(text) == _reference(text)
    assert _top(_live(text)) == TOP


def test_one_row_with_a_different_layout_does_not_merge_two_rows() -> None:
    # 둘째 행만 spark 다음이 age — 다음 행의 `],"status":` 까지 건너뛰면 BBB 와 CCC 가 한 행이 된다
    rows = _rows()
    keys = list(rows[1])
    keys.remove("age")
    rows[1] = _reorder(
        rows[1],
        keys[: keys.index("spark") + 1] + ["age"] + keys[keys.index("spark") + 1 :],
    )
    text = _compact(rows)
    assert _strip_spark(text) is text
    live = _live(text)
    assert live == _reference(text)
    assert _top(live) == TOP and live["pairs"] == 4


def test_nested_list_in_spark_falls_back_to_full_parse() -> None:
    # 계약 밖(숫자 목록이 아님)이지만 JSON 으로는 맞다. 둘째는 안쪽 목록의 `]` 바로 뒤가 `,"status":` 라
    # 끝 표식만 보면 목록의 끝으로 잘못 읽는다 — 목록 안의 `[` 를 보고 원문 파싱으로 되돌아가야 한다
    for spark in ([[1.0, 2.0], [3.0]], [{"a": [1.0], "status": 2}]):
        rows = _rows()
        rows[2]["spark"] = spark
        text = _compact(rows)
        assert _strip_spark(text) is text
        assert _live(text) == _reference(text)
        assert _top(_live(text)) == TOP


def test_empty_spark_and_marker_text_inside_strings() -> None:
    rows = _rows()
    rows[0]["spark"] = []
    # 문자열 안의 표식 글자는 따옴표가 `\"` 로 적혀 표식과 맞지 않는다 — 망 이름이 그대로 실려 나와야 한다
    rows[0]["netDom"] = 'x,"spark":[1],"status":"ok"'
    rows[3]["netFx"] = '],"status":"fail"'
    text = _compact(rows)
    stripped = _strip_spark(text)
    assert '"spark"' not in stripped
    live = _live(text)
    assert live == _reference(text)
    assert live["top"][0]["net_dom"] == 'x,"spark":[1],"status":"ok"'
    assert live["top"][1]["net_fx"] == '],"status":"fail"'


def test_spark_as_the_first_key_is_left_in_place_and_others_are_stripped() -> None:
    rows = _rows()
    rows[1] = _reorder(rows[1], ["spark"] + [k for k in rows[1] if k != "spark"])
    text = _compact(rows)
    stripped = _strip_spark(text)
    assert (
        stripped.count('"spark"') == 1
    )  # 앞에 콤마가 없는 첫 키는 표식과 맞지 않아 그대로 파싱한다
    assert _live(text) == _reference(text)


def test_truncated_text_is_null_like_the_full_parse() -> None:
    text = _compact(_rows())
    cut_in_spark = text[: text.index('"spark":[') + 14]
    cut_after_spark = text[: text.index('],"status":') + 15]
    for t in (cut_in_spark, cut_after_spark, text[:-1], ""):
        assert build_live(t) is None


def test_table_without_rows_list_is_null_and_no_rows_is_an_empty_live() -> None:
    assert build_live('{"rate":1360.0,"rows":"x"}') is None
    assert build_live("[]") is None
    live = _live(_compact([]))
    assert live is not None and (live["pairs"], live["top"]) == (0, [])
