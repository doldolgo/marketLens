"""선선갭 표 계산 — 틱마다 (perp 원천 쌍 × 양쪽에 행이 있는 base) 1장 (스펙 049 §3.2).

가격갭은 048 과 같이 체결 가능한 최우선 호가로 잰다(2026-10-08 사람 결정) — 싼 쪽 ask 에 롱, 비싼 쪽 bid 에 숏.
순서 없는 쌍 하나가 행 하나이고 두 방향(`longA`·`longB`)을 다 싣는다 — 방향 선택과 시간당 펀딩·펀딩갭은 화면이 한다.
틱 루프 안에서 동기로 끝난다 — `await` 가 없어 한 표 안에서 교체 전후 호가가 섞이지 않는다.

표 계산 경로 규칙(048 과 같다): 스트림 age 는 원천마다 한 번, 시장(원천, 코인) 재료(age·최우선 검사·펀딩 글)는
코인마다 한 번 만들고, 행은 코인 → 원천 순서 쌍으로 만들어 정렬하지 않는다.

행 계산 고리는 한 벌(`_rows`)이고 행을 내보내는 자리만 두 갈래다(048 과 같은 이유).
- dict 갈래(`build_pp_table`): 행은 키 순서 고정 dict — 테스트의 대조 기준이고, 글 갈래가 멈춘 회차에 게시기가 쓴다.
- 글 갈래(`build_pp_text`, 049 게시기 전용): 행 dict 없이 표 JSON 글을 바로 쓴다 — `encode_pp_table(build_pp_table())`
  와 같은 바이트다. 표 1장이 1,500행 안팎이라 행마다 13키 dict 를 만들고 인코더가 다시 훑는 일을 없앤다.
"""

import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import PERP_SOURCES
from app.core.live_store import LiveStore

# 003 §3.2 와 같은 규칙 — age 5초면 stale, 행 자체가 300초 안 바뀌면 그 행의 실제 경과 초
STALE_AGE_SEC = 5.0
ROW_STALE_SEC = 300.0
_ROW_STALE = timedelta(seconds=ROW_STALE_SEC)

# 글 갈래가 값 하나씩 쓸 때의 인코더 — 게시기 `encode_pp_table` 과 같은 설정(공백 없음·NaN 거부·비ASCII 그대로)
_JSON = json.JSONEncoder(
    ensure_ascii=False, allow_nan=False, separators=(",", ":")
).encode
# 이름 글(코인·원천 id → 따옴표·이스케이프를 포함한 JSON 문자열 글) 기억. 값이 이름의 순수 함수라 오래된 값이 남을
# 걱정이 없다. 코인 약 450·id 4개라 상한에 닿지 않고, 닿으면 비우고 다시 채운다(값은 같다)
_NAME_TEXTS: dict[str, str] = {}
_NAME_TEXTS_MAX = 8192


def _name_text(name: str) -> str:
    text = _NAME_TEXTS.get(name)
    if text is None:
        if len(_NAME_TEXTS) >= _NAME_TEXTS_MAX:
            _NAME_TEXTS.clear()
        text = _NAME_TEXTS[name] = _JSON(name)
    return text


def _num_text(value: Any) -> str:
    """시장 단위 숫자(펀딩 세 값)의 글 — 행 dict 를 `encode_pp_table` 로 인코딩할 때와 같은 글.

    유한한 float 는 repr(json 의 float 표기와 같다), int 는 repr, None 은 null. 그 밖(inf·NaN·bool·다른 형)은 같은
    인코더에 넘긴다 — 같은 글(bool → true)이 나오거나 같은 ValueError·TypeError 가 난다.
    """
    tp = type(value)
    if tp is float:
        if value - value == 0.0:
            return repr(value)
        return _JSON(value)
    if tp is int:
        return repr(value)
    if value is None:
        return "null"
    return _JSON(value)


def _rows(store: LiveStore, now: datetime, as_text: bool) -> list[Any]:
    """표 1장의 행 — `as_text` 면 행 JSON 글, 아니면 13키 dict. 행 계산·순서·assert 는 두 갈래가 같다.

    글 갈래는 유한하지 않은 값이 든 행을 만나면 `ValueError` 를 낸다(값 이상한 형은 `TypeError`). 게시기는 그때
    dict 갈래로 다시 만들어 기준과 같은 결과(같은 글이나 같은 예외)를 낸다 — 정상 회차에는 일어나지 않는다.
    """
    now_ms = now.timestamp() * 1000
    cutoff = now - _ROW_STALE

    # 원천 표는 저장소의 표 그 자체(BASE 대문자 → 행)를 읽기만 한다 — 원천 순서 그대로
    tables: list[tuple[str, dict[str, Any]]] = []
    for source in PERP_SOURCES:
        table = store.perp_table(source)
        if table:
            tables.append((source, table))
    # 코인 범위 = perp 우주 전체(국내 상장과 무관) 중 원천 2곳 이상에 행이 있는 코인 — 한 곳뿐이면 쌍이 없다 (§3.6)
    seen: set[str] = set()
    bases: set[str] = set()
    for _, table in tables:
        keys = table.keys()
        bases.update(keys & seen)
        seen.update(keys)

    stream_ages: dict[str, float] = {}
    # 표 1장 안의 age → 글. age 는 0.0 에서 시작해 더 클 때만 바뀌므로 −0.0·NaN 이 키로 오지 않는다 — 같은 키면 같은 글
    age_texts: dict[float, str] = {}
    out: list[Any] = []
    append = out.append
    stale_age = STALE_AGE_SEC
    # 한 코인의 시장 재료 — 원천 순서. 코인마다 비우고 다시 채운다
    markets: list[tuple[Any, ...]] = []
    for base in sorted(bases):
        markets.clear()
        for source, table in tables:
            prow = table.get(base)
            if prow is None:
                continue
            # 시장(원천, 코인) 재료 — 스트림 age 는 원천마다 표 1장에 한 번
            page = stream_ages.get(source)
            if page is None:
                state = store.stream_state(source)
                # 행은 스트림 메시지(·폴링 성공 회차)로만 생기므로 행이 있는 원천의 수신 시각은 항상 있다
                assert state is not None and state.last_message_at is not None
                page = stream_ages[source] = (now_ms - state.last_message_at) / 1000
            updated = prow.updated_at
            assert updated is not None
            if updated <= cutoff:
                # 거래 정지·심볼 장애 — 스트림은 살아 있는데 이 코인 프레임만 안 온다 (003 §3.2-4 와 같다)
                page = max(page, (now - updated).total_seconds())
            pok = (
                prow.bid > 0
                and prow.bid_size > 0
                and prow.ask > 0
                and prow.ask_size > 0
            )
            # 펀딩 세 값은 fail 행에도 그대로 싣는다 — 호가와 무관한 값이다 (§3.2)
            rate = prow.funding_rate
            funding = None if rate is None else rate * 100  # 한 주기의 비율 → 퍼센트
            next_ms = prow.next_funding_ms
            # product.md 시각 규칙 — `*Ts`·`next*` 는 epoch 초
            next_ts = None if next_ms is None else next_ms // 1000
            interval_h = prow.funding_interval_h
            if as_text:
                # 글 갈래 — 원천 id 글과 a 자리·b 자리 펀딩 꼬리 글을 시장마다 한 번. 코인에 원천이 2곳 이상이라
                # 시장은 늘 행 1개 이상에 실리므로 여기서 난 ValueError 는 dict 갈래 인코딩도 낸다
                fund_text = _num_text(funding)
                int_text = _num_text(interval_h)
                next_text = _num_text(next_ts)
                markets.append(
                    (
                        page,
                        prow.bid,
                        prow.ask,
                        pok,
                        _name_text(source),
                        f',"fundA":{fund_text},"intA":{int_text},"nextA":{next_text}',
                        f',"fundB":{fund_text},"intB":{int_text},"nextB":{next_text},"status":"',
                    )
                )
            else:
                markets.append(
                    (
                        page,
                        prow.bid,
                        prow.ask,
                        pok,
                        source,
                        funding,
                        interval_h,
                        next_ts,
                    )
                )
        if as_text:
            sym_text = _name_text(base)
        n = len(markets)
        for i in range(n - 1):
            ma = markets[i]
            aage = ma[0]
            if as_text:
                head = f'{{"sym":{sym_text},"a":{ma[4]},"b":'
            for j in range(i + 1, n):
                mb = markets[j]
                # age 는 양측 스트림 중 오래된 쪽 — max(0.0, a, b) 와 같은 값(앞에서부터 더 큰 것만 바꾼다)
                age = 0.0
                if aage > age:
                    age = aage
                if mb[0] > age:
                    age = mb[0]
                if ma[3] and mb[3]:
                    # longA: a 의 ask 에 롱·b 의 bid 에 숏, longB: b 의 ask 에 롱·a 의 bid 에 숏. 식은 core
                    # `premium_percent`(sell / buy − 1) × 100 그대로 옮겨 적었다(같은 연산·같은 순서라 비트까지 같다)
                    long_a = (mb[1] / ma[2] - 1) * 100
                    long_b = (ma[1] / mb[2] - 1) * 100
                    status = "stale" if age >= stale_age else "ok"
                else:
                    # 호가 4개 중 하나라도 없거나 0 이하 — 두 값은 0, 펀딩은 그대로 (§3.2)
                    long_a = long_b = 0.0
                    status = "fail"
                if not as_text:
                    append(
                        {
                            "sym": base,
                            "a": ma[4],
                            "b": mb[4],
                            "longA": long_a,
                            "longB": long_b,
                            "fundA": ma[5],
                            "intA": ma[6],
                            "nextA": ma[7],
                            "fundB": mb[5],
                            "intB": mb[6],
                            "nextB": mb[7],
                            "status": status,
                            "age": age,
                        }
                    )
                    continue

                # --- 글 갈래 — 위 dict 를 `encode_pp_table` 로 인코딩한 것과 같은 글 ---
                age_text = age_texts.get(age)
                if age_text is None:
                    # age 는 늘 float 다. 유한하지 않으면 인코더가 같은 ValueError
                    age_text = age_texts[age] = (
                        repr(age) if age - age == 0.0 else _JSON(age)
                    )
                if status == "fail":
                    append(
                        f'{head}{mb[4]},"longA":0.0,"longB":0.0{ma[5]}{mb[6]}fail","age":{age_text}}}'
                    )
                    continue
                # 두 값은 float 끼리의 나눗셈이라 늘 float 다. 유한성은 합 하나로 본다 — 하나라도 NaN·±inf 면 합이
                # NaN·±inf 라 `chk - chk` 가 0 이 아니다(유한한 둘의 합이 넘쳐도 걸린다 — 그때는 게시기가 dict
                # 갈래로 다시 만들어 기준 바이트를 낸다)
                chk = long_a + long_b
                if chk - chk != 0.0:
                    raise ValueError(
                        f"선선표 행에 유한하지 않은 값 — {base} {ma[4]}/{mb[4]}"
                    )
                append(
                    f'{head}{mb[4]},"longA":{long_a!r},"longB":{long_b!r}'
                    f'{ma[5]}{mb[6]}{status}","age":{age_text}}}'
                )
    return out


def build_pp_table(
    store: LiveStore, *, now: datetime | None = None
) -> dict[str, object]:
    """선선갭 표 1장 — `{"rows", "warnings", "dataReceivedAt", "fetchedAt"}`, 행 13키 (§3.2).

    조합 = (perp 원천 a, perp 원천 b, 양쪽에 행이 있는 base), a 가 원천 순서에서 b 보다 앞 — 순서 없는 쌍 하나가 행
    하나(코인당 최대 6행). 행 정렬은 (sym, a, b) — a·b 는 046 원천 순서(사전순이 아니다). `warnings` 는 지금 항상 빈
    목록이다(017 과 모양을 맞추기 위해 둔다). 게시기는 `build_pp_text`(같은 바이트)를 쓰고, 이 함수는 테스트·대조
    기준과 글 갈래가 멈춘 회차에 쓰인다.
    """
    if now is None:
        now = datetime.now(UTC)
    rows = _rows(store, now, False)
    return {
        "rows": rows,
        "warnings": [],
        "dataReceivedAt": store.received_at,
        "fetchedAt": int(time.time() * 1000),
    }


def build_pp_text(store: LiveStore, *, now: datetime | None = None) -> str:
    """선선갭 표 1장의 JSON 글 — `encode_pp_table(build_pp_table(store, now=now))` 와 같은 바이트 (§3.3).

    행은 dict 없이 글로 바로 쓴다: 키는 글자 상수, 이름은 기억한 JSON 문자열 글, 펀딩 세 값 꼬리는 시장마다 a 자리·
    b 자리 한 번씩, age 는 표 1장 안에서 같은 값의 글을 다시 쓴다. 유한하지 않은 값이 든 표는 `ValueError` 로 멈춘다 —
    게시기(`build_pp_json`)가 dict 갈래로 다시 만든다. 최상위 키 순서는 `build_pp_table` 과 같다.
    """
    if now is None:
        now = datetime.now(UTC)
    rows = _rows(store, now, True)
    return (
        '{"rows":['
        + ",".join(rows)
        + '],"warnings":[],"dataReceivedAt":'
        + _JSON(store.received_at)
        + ',"fetchedAt":'
        + repr(int(time.time() * 1000))
        + "}"
    )
