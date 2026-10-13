"""현선갭 표 계산 — 틱마다 (해외 현물 거래소 × perp 원천 × 양쪽에 행이 있는 base) 1장 (스펙 048 §3.2).

갭은 체결 가능한 최우선 호가로 잰다(2026-10-08 사람 결정) — 현물은 ask 에 사고 perp 는 bid 에 숏하는 값이라
체결가·mark 기준보다 보수적이고, 양수면 그 순간 실제로 벌어져 있는 것이다. 슬리피지 차감은 없다(최우선 1단계만).
틱 루프 안에서 동기로 끝난다 — `await` 가 없어 한 표 안에서 교체 전후 호가가 섞이지 않는다.

표 계산 경로 규칙(003 §3.2-7 과 같다): 스트림 age 는 거래소·원천마다 한 번, 시장(거래소, 코인) 재료(age·최우선
검사)는 처음 쓰일 때 한 번 만들고, 행은 코인 → 현물 거래소 순서 → perp 원천 순서로 만들어 정렬하지 않는다.

행 계산 고리는 한 벌(`_rows`)이고 행을 내보내는 자리만 두 갈래다(2026-10-09).
- dict 갈래(`build_gap_table`): 행은 키 순서 고정 dict 다 — `json.dumps` 가 NamedTuple 을 객체가 아니라 배열로
  내보내서, 스펙의 "불변 튜플형 레코드" 대신 003 의 뜨거운 경로와 같은 모양을 쓴다. 테스트의 대조 기준이고,
  글 갈래가 멈춘 회차에 게시기가 이 갈래로 다시 만든다.
- 글 갈래(`build_gap_text`, 048 게시기 전용): 행 dict 없이 표 JSON 글을 바로 쓴다 — `encode_gap_table(build_gap_table())`
  와 같은 바이트다. 표 1장이 약 3,400행이라 행마다 11키 dict 를 만들고 C 인코더가 그 dict 를 다시 훑던 일과,
  인코딩이 끝날 때까지 살아 있는 dict 3천여 개가 0세대 계수를 문턱(2000) 넘게 올려 이 단계 안에서 돌던 GC 를 덜려는
  것이다(재생 실측 갭표 평균 8.8 → 5.1ms, 단계 안 GC 0회).
"""

import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import DOMESTIC_EXCHANGES, EXCHANGES, PERP_SOURCES
from app.core.live_store import LiveStore
from app.core.models import PerpRow, Row

# 해외 현물 거래소 = 001 §3.2 의 해외 원천 전부, 그 순서 (048 §3.1). 국내 거래소는 현선갭에 끼지 않는다
SPOT_EXCHANGES = tuple(ex for ex in EXCHANGES if ex not in DOMESTIC_EXCHANGES)
# 003 §3.2 와 같은 규칙 — age 5초면 stale, 행 자체가 300초 안 바뀌면 그 행의 실제 경과 초
STALE_AGE_SEC = 5.0
ROW_STALE_SEC = 300.0
_ROW_STALE = timedelta(seconds=ROW_STALE_SEC)

# 글 갈래가 값 하나씩 쓸 때의 인코더 — 게시기 `encode_gap_table` 과 같은 설정(공백 없음·NaN 거부·비ASCII 그대로)
_JSON = json.JSONEncoder(
    ensure_ascii=False, allow_nan=False, separators=(",", ":")
).encode
# 이름 글(코인·거래소·원천 id → 따옴표·이스케이프를 포함한 JSON 문자열 글) 기억. 값이 이름의 순수 함수라 오래된
# 값이 남을 걱정이 없다. 코인 약 360·id 8개라 상한에 닿지 않고, 닿으면 비우고 다시 채운다(값은 같다)
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
    """시장 단위 숫자(현물가·펀딩 세 값)의 글 — 행 dict 를 `encode_gap_table` 로 인코딩할 때와 같은 글.

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
    """표 1장의 행 — `as_text` 면 행 JSON 글, 아니면 11키 dict. 행 계산·순서·assert 는 두 갈래가 같다.

    글 갈래는 유한하지 않은 값이 든 행을 만나면 `ValueError` 를 낸다(값 이상한 형은 `TypeError`). 게시기는 그때
    dict 갈래로 다시 만들어 기준과 같은 결과(같은 글이나 같은 예외)를 낸다 — 정상 회차에는 일어나지 않는다.
    """
    now_ms = now.timestamp() * 1000
    cutoff = now - _ROW_STALE

    # 표 훑기는 get_all·perp_rows 공개 함수로 — 저장소 키(BASE 대문자)를 행에서 다시 만든다. 기준과 같은 키·순서다
    # 거래소·원천 id 글(글 갈래만)은 표마다 한 번 꺼내 둔다
    spot_tables: list[tuple[str, dict[str, Row], str | None]] = []
    spot_bases: set[str] = set()
    for ex in SPOT_EXCHANGES:
        rows = store.get_all(exchange=ex)
        if rows:
            spot_table = {r.base.upper(): r for r in rows}
            spot_tables.append((ex, spot_table, _name_text(ex) if as_text else None))
            spot_bases.update(spot_table)
    perp_tables: list[tuple[str, dict[str, PerpRow], str | None]] = []
    perp_bases: set[str] = set()
    for source in PERP_SOURCES:
        prows = store.perp_rows(source=source)
        if prows:
            perp_table = {r.base.upper(): r for r in prows}
            perp_tables.append(
                (source, perp_table, _name_text(source) if as_text else None)
            )
            perp_bases.update(perp_table)

    stream_ages: dict[str, float] = {}
    # 표 1장 안의 age → 글. age 는 0.0 에서 시작해 더 클 때만 바뀌므로(행 고리의 비교) −0.0·NaN 이 키로 오지 않는다
    # — 같은 키면 같은 글이다. 고유값은 스트림 쌍 수와 300초 예외 행 수 정도뿐이다
    age_texts: dict[float, str] = {}
    out: list[Any] = []
    append = out.append
    stale_age = STALE_AGE_SEC
    # 한 코인의 perp 재료 — 현물 거래소마다 다시 만들지 않는다. 코인마다 비우고 다시 채운다
    perp_markets: list[tuple[Any, ...]] = []
    # 현선갭의 코인 범위 = 김프 우주(현물 행이 있는 코인) ∩ perp 우주 — 한쪽만 있는 코인은 행이 없다 (§3.7)
    for base in sorted(spot_bases & perp_bases):
        perp_markets.clear()
        for source, perp_table, source_text in perp_tables:
            prow = perp_table.get(base)
            if prow is None:
                continue
            # perp 시장(원천, 코인) 재료 — 스트림 age 는 원천마다 표 1장에 한 번
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
            # product.md 시각 규칙 — `*Ts` 는 epoch 초
            next_ts = None if next_ms is None else next_ms // 1000
            interval_h = prow.funding_interval_h
            if as_text:
                # 글 갈래 — 원천 id 글과 펀딩 세 값 꼬리 글을 시장마다 한 번. perp 시장은 늘 행 1개 이상에 실리므로
                # (코인이 현물 쪽에도 있다) 여기서 난 ValueError 는 dict 갈래 인코딩도 낸다
                perp_markets.append(
                    (
                        page,
                        prow.bid,
                        prow.ask,
                        pok,
                        source_text,
                        f',"funding":{_num_text(funding)},"intervalH":{_num_text(interval_h)}'
                        f',"nextFundingTs":{_num_text(next_ts)},"status":"',
                    )
                )
            else:
                perp_markets.append(
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
        for spot, spot_table, spot_text in spot_tables:
            srow = spot_table.get(base)
            if srow is None:
                continue
            # 현물 시장(거래소, 코인) 재료 — 스트림 age·최우선 검사를 시장마다 한 번
            sage = stream_ages.get(spot)
            if sage is None:
                state = store.stream_state(spot)
                assert state is not None and state.last_message_at is not None
                sage = stream_ages[spot] = (now_ms - state.last_message_at) / 1000
            updated = srow.updated_at
            assert updated is not None
            if updated <= cutoff:
                sage = max(sage, (now - updated).total_seconds())
            bid = srow.bids[0] if srow.bids else None
            ask = srow.asks[0] if srow.asks else None
            # 잔량도 본다 — 잔량 0 인 호가는 체결할 수 없으니 갭이 아니다
            sok = not (
                bid is None
                or ask is None
                or bid[0] <= 0
                or bid[1] <= 0
                or ask[0] <= 0
                or ask[1] <= 0
            )
            if sok:
                s_bid = bid[0]  # type: ignore[index]
                s_ask = ask[0]  # type: ignore[index]
            price = srow.price
            if as_text:
                head = f'{{"sym":{sym_text},"spot":{spot_text},"perp":'
                # 현물가 글은 ok·stale 행이 처음 쓸 때 한 번 — fail 행만 있는 시장은 기준처럼 현물가를 보지 않는다
                price_text = None
            for market in perp_markets:
                page = market[0]
                # age 는 양측 스트림 중 오래된 쪽 — max(0.0, 현물, perp) 와 같은 값(앞에서부터 더 큰 것만 바꾼다)
                age = 0.0
                if sage > age:
                    age = sage
                if page > age:
                    age = page
                if sok and market[3]:
                    # 진입: 현물 ask 에 사고 perp bid 에 숏 — 클수록 좋다. 정리: 현물 bid 에 팔고 perp ask 에 숏 청산 —
                    # 작을수록 좋다. 식은 core `premium_percent`(sell / buy − 1) × 100 그대로 — 행마다 키워드 호출을
                    # 하지 않으려고 옮겨 적었다(같은 연산·같은 순서라 비트까지 같다)
                    entry = (market[1] / s_ask - 1) * 100
                    exit_ = (market[2] / s_bid - 1) * 100
                    status = "stale" if age >= stale_age else "ok"
                else:
                    # 호가 4개 중 하나라도 없거나 0 이하 — 숫자 셋은 0, 펀딩은 그대로 (§3.2)
                    status = "fail"
                if not as_text:
                    if status == "fail":
                        append(
                            {
                                "sym": base,
                                "spot": spot,
                                "perp": market[4],
                                "spotPrice": 0.0,
                                "entry": 0.0,
                                "exit": 0.0,
                                "funding": market[5],
                                "intervalH": market[6],
                                "nextFundingTs": market[7],
                                "status": status,
                                "age": age,
                            }
                        )
                    else:
                        append(
                            {
                                "sym": base,
                                "spot": spot,
                                "perp": market[4],
                                "spotPrice": price,
                                "entry": entry,
                                "exit": exit_,
                                "funding": market[5],
                                "intervalH": market[6],
                                "nextFundingTs": market[7],
                                "status": status,
                                "age": age,
                            }
                        )
                    continue

                # --- 글 갈래 — 위 dict 를 `encode_gap_table` 로 인코딩한 것과 같은 글 ---
                age_text = age_texts.get(age)
                if age_text is None:
                    # age 는 늘 float(나눗셈·total_seconds 결과 또는 0.0)다. 유한하지 않으면 인코더가 같은 ValueError
                    age_text = age_texts[age] = (
                        repr(age) if age - age == 0.0 else _JSON(age)
                    )
                if status == "fail":
                    append(
                        f'{head}{market[4]},"spotPrice":0.0,"entry":0.0,"exit":0.0'
                        f'{market[5]}fail","age":{age_text}}}'
                    )
                    continue
                # entry·exit 는 int·float 끼리의 나눗셈이라 늘 float 다. 유한성은 합 하나로 본다 — 하나라도 NaN·±inf 면
                # 합이 NaN·±inf 라 `chk - chk` 가 0 이 아니다(유한한 둘의 합이 넘쳐도 걸린다 — 그때는 게시기가 dict
                # 갈래로 다시 만들어 기준 바이트를 낸다)
                chk = entry + exit_
                if chk - chk != 0.0:
                    raise ValueError(
                        f"갭표 행에 유한하지 않은 값 — {base} {spot}/{market[4]}"
                    )
                if price_text is None:
                    price_text = _num_text(price)
                append(
                    f'{head}{market[4]},"spotPrice":{price_text},"entry":{entry!r},'
                    f'"exit":{exit_!r}{market[5]}{status}","age":{age_text}}}'
                )
    return out


def build_gap_table(
    store: LiveStore, *, now: datetime | None = None
) -> dict[str, object]:
    """현선갭 표 1장 — `{"rows", "warnings", "dataReceivedAt", "fetchedAt"}`, 행 11키 (§3.2).

    조합 = 해외 현물 거래소 × perp 원천 × 양쪽에 행이 있는 base. 같은 회사의 현물·perp(`binance` × `binance_perp`)도
    한 행이다 — 한 거래소 안의 베이시스 거래가 가장 흔하다. 행 정렬은 (sym, spot, perp) — spot 은 해외 현물 거래소 순서,
    perp 는 046 원천 순서(사전순이 아니다). `warnings` 는 지금 항상 빈 목록이다(017 과 모양을 맞추기 위해 둔다).
    게시기는 `build_gap_text`(같은 바이트)를 쓰고, 이 함수는 테스트·대조 기준과 글 갈래가 멈춘 회차에 쓰인다.
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


def build_gap_text(store: LiveStore, *, now: datetime | None = None) -> str:
    """현선갭 표 1장의 JSON 글 — `encode_gap_table(build_gap_table(store, now=now))` 와 같은 바이트 (§3.3).

    행은 dict 없이 글로 바로 쓴다: 키는 글자 상수, 이름은 기억한 JSON 문자열 글, 현물가는 현물 시장마다·펀딩 세 값
    꼬리는 perp 시장마다 한 번, age 는 표 1장 안에서 같은 값의 글을 다시 쓴다. 유한하지 않은 값이 든 표는
    `ValueError` 로 멈춘다 — 게시기(`build_gap_json`)가 dict 갈래로 다시 만든다. 최상위 키 순서는
    `build_gap_table` 과 같다(rows·warnings·dataReceivedAt·fetchedAt).
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
