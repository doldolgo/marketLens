"""서빙 프로세스의 구독·diff·브로드캐스트 (스펙 017 §3.2).

Redis 채널 `spreads` 를 구독하는 태스크 하나가 새 표마다 diff 를 1회 만들고 gzip 도 1회 해서, 접속자
전원에게 같은 바이트를 넣는다. 압축을 여기서 한 번만 하는 이유 — uvicorn 의 permessage-deflate 는 접속마다
따로 압축해서 api CPU 가 접속자 수에 비례했다(실측 2026-09-26: 50명에 1코어 포화). 그래서 Dockerfile 이
그 압축을 끄고, 브라우저는 gzip 바이너리 프레임을 DecompressionStream 으로 푼다. 새 접속자에게 주는
snapshot 도 표 1장당 한 번만 압축해 둔다(2026-09-28) — 배포 직후 전원이 다시 붙을 때 접속마다 790KB 를
압축하면 루프가 수 초씩 멈췄다.
접속자마다 보내기 대기열·보내기 태스크가 따로 있어 느린 한 명이 나머지를 막지 않는다.
채널은 첫 접속자가 붙을 때 구독하고 마지막 접속자가 떠난 뒤 30초가 지나면 닫는다(2026-09-28) — 접속자가
없는 허브(배포의 `server` 컨테이너, 아무도 안 보는 api)가 매초 790KB 를 받아 버리지 않게.
허브는 표 id 하나로 매개변수화된다(048 §3.4) — 채널 `<id>`·시작 키 `<id>:latest`·행 키 함수만 다르고 나머지 규칙은
같다. 인스턴스와 구독 연결은 표마다 하나(`spreads`·`gap`·`pp`)이고 보는 사람 흔적 `<id>:want` 도 표마다 따로 쓴다 —
`spreads:want` 는 spreads 허브만, `gap:want` 는 gap 허브만, `pp:want` 는 pp 허브만. 수집은 `gap:want`·`pp:want` 가 있을
때만 그 표를 만든다(048 §3.3·049 §3.3).

표는 파싱하지 않는다(2026-10-09). 수집의 게시기가 쓴 표 글(공백 없음·ensure_ascii=False)을 행 조각으로 잘라 `age` 를
뺀 글끼리 비교하고, 바뀐 행은 받은 조각을 그대로 이어 delta 를 만든다. snapshot 은 표 글 앞에 `type` 키만 끼운다.
표 1장(spreads 약 1,800행·930KB, gap 약 3,400행·710KB)을 매초 json.loads → 행 사본 → json.dumps 하던 왕복을 없앤
것이고, 나가는 바이트는 파싱해서 다시 쓰던 때와 같다. 글 모양이 다르면(공백 있는 인코더 등) 예전처럼 파싱해서 diff 한다.
gzip 은 허브마다 전용 스레드 1개가 한다(2026-10-09). zlib 은 압축하는 동안 GIL 을 놓으므로 표 1장의 압축(로컬 spreads
delta 약 16ms·snapshot 약 33ms)이 이벤트 루프를 멈추지 않는다. 프레임은 만든 순서대로 줄을 서고 앞 프레임의 압축이
끝나야 뒤 프레임이 나간다. 받을 접속자는 프레임을 만든 순간에 정한다 — 압축을 기다리는 사이 붙은 접속자는 그 뒤에
만든 프레임만 받는다. 그래서 접속마다 받는 프레임과 순서는 루프에서 동기로 압축하던 때와 같다.
"""

import asyncio
import concurrent.futures
import contextlib
import functools
import gzip
import json
import logging
import re
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from fastapi import WebSocket

from app.core.redis_bus import CHANNEL, GAP_CHANNEL, PP_CHANNEL, RedisBus

logger = logging.getLogger("marketlens.spreads_hub")

HEARTBEAT_SEC = 1.0  # 이만큼 아무것도 안 보냈으면 heartbeat (§3.2)
SEND_QUEUE_LIMIT = 5  # 대기열이 이만큼 쌓인 접속자는 1008 로 닫는다 (§3.2)
WANT_REFRESH_SEC = 5.0
SUB_POLL_SEC = 1.0  # 구독 메시지 대기 단위 — want 갱신·취소·유휴 확인 주기
IDLE_UNSUBSCRIBE_SEC = (
    30.0  # 마지막 접속자가 떠난 뒤 이만큼 지나면 구독을 닫는다 (§3.2)
)
BACKOFF_MIN_SEC = 1.0
BACKOFF_MAX_SEC = 30.0
CODE_SLOW = 1008
CODE_GOING_AWAY = 1001
GZIP_LEVEL = 6  # 표 1장당 1회라 CPU 보다 크기를 택한다 (9 는 시간이 2배인데 크기 차이가 거의 없다)
# 이 글자 수보다 짧은 메시지는 루프에서 바로 압축한다 — 16KB 압축이 로컬 0.24ms 라 스레드로 넘겨도 루프가 아낄 것이
# 없고 프레임만 루프 몇 바퀴 늦게 나간다. 실제 표의 delta·snapshot(수백 KB)은 전부 스레드로 간다
THREAD_PACK_MIN = 16 * 1024
SHAPE_WARN_SEC = 60.0  # 표 글 모양이 달라 파싱 경로로 간 것을 알리는 경고 간격


def pack(text: str) -> bytes:
    """브라우저로 나가는 프레임 1개 — 모든 메시지가 같은 방식이라 클라이언트가 분기하지 않는다."""
    return gzip.compress(text.encode(), compresslevel=GZIP_LEVEL)


HEARTBEAT = pack('{"type":"heartbeat"}')
WAITING = pack('{"type":"waiting"}')


# --- diff 순수 함수 (§3.2) ---

RowKey = Callable[[dict], str]


def row_key(row: dict) -> str:
    return f"{row['sym']}|{row['dom']}|{row['fx']}"


def gap_row_key(row: dict) -> str:
    """048 §3.4 — 현선갭 행의 키 `sym|spot|perp`."""
    return f"{row['sym']}|{row['spot']}|{row['perp']}"


def pp_row_key(row: dict) -> str:
    """049 §3.3 — 선선갭 행의 키 `sym|a|b`."""
    return f"{row['sym']}|{row['a']}|{row['b']}"


# 표 id → 행 키 함수. 허브가 다루는 표는 이 셋뿐이다 (048 §3.4·049 §3.3)
ROW_KEYS: dict[str, RowKey] = {
    CHANNEL: row_key,
    GAP_CHANNEL: gap_row_key,
    PP_CHANNEL: pp_row_key,
}


def index_rows(table: dict, key: RowKey = row_key) -> dict[str, dict]:
    """키 → `age` 를 뺀 행. `age` 만 다른 행을 "안 바뀜" 으로 보기 위한 비교 형태."""
    return {key(r): {k: v for k, v in r.items() if k != "age"} for r in table["rows"]}


def make_snapshot(table: dict) -> str:
    return _dumps({"type": "snapshot", **table})


def make_delta(
    prev: dict[str, dict], table: dict, key: RowKey = row_key
) -> tuple[str, dict[str, dict]]:
    """직전 인덱스와 새 표 → (delta 메시지, 새 인덱스). 바뀐 행은 현재 `age` 를 그대로 싣는다.

    메타는 `rows` 를 뺀 최상위 전부 — spreads 는 notional·rate·warnings·시각 둘, gap 은 warnings·시각 둘(048 §3.4).
    표마다 키 목록을 따로 들지 않아도 두 표의 바이트가 각각 그대로다.
    허브는 표 글이 게시기 모양이 아닐 때만 이 파싱 경로를 쓴다 — 글 경로(`make_delta_text`)가 맞춰야 할 기준이기도 하다.
    """
    cur = index_rows(table, key)
    changed = [r for r in table["rows"] if prev.get(key(r)) != cur[key(r)]]
    removed = [k for k in prev if k not in cur]
    delta = {"type": "delta", **{k: v for k, v in table.items() if k != "rows"}}
    delta["rows"] = changed
    delta["removed"] = removed
    return _dumps(delta), cur


def _dumps(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


# --- 표 글을 파싱하지 않는 diff (2026-10-09) ---
# 표 글은 수집의 게시기 인코더(017 §3.1·048 §3.3 — ensure_ascii=False, 공백 없음, NaN 거부)가 평평한 행 dict 를 쓴
# 것이다. 같은 값은 늘 같은 글이 되고(문자열 이스케이프는 하나로 정해져 있고 float 는 repr 왕복), 파싱했다 같은 설정으로
# 다시 쓰면 받은 글 그대로다. 그래서 행 조각 글끼리 비교하고 조각을 그대로 이어 붙이면 파싱 경로와 바이트가 같다.
# JSON 문자열 안의 따옴표는 `\"` 로 나가므로 아래 표식(맨 따옴표가 든 글자)은 문자열 안에 생기지 않는다.
ROWS_OPEN = '"rows":['
ROWS_CLOSE = '],"warnings":'  # 세 표 모두 최상위에서 rows 다음 키가 warnings 다 (003 build_table·048 build_gap_table·049 build_pp_table)
ROW_HEAD = '{"sym":'  # 행은 늘 sym 으로 시작한다
ROW_SEP = '},{"sym":'
AGE = ',"age":'
# 표 id → 행 키 필드 셋 바로 뒤 필드의 표식. 행 조각에서 이 표식 앞이 키 글자다(spreads sym·dom·fx, gap sym·spot·perp,
# pp sym·a·b)
KEY_ENDS: dict[str, str] = {
    CHANNEL: ',"fwd":',
    GAP_CHANNEL: ',"spotPrice":',
    PP_CHANNEL: ',"longA":',
}
_JSON_STR = r'"(?:[^"\\]|\\.)*"'
# 키 글자의 꼴 — 표마다 첫 행 하나로 키 필드 이름·순서를 본다. 계약이 바뀌어 키 필드와 표식 사이에 필드가 끼면 파싱 경로로
KEY_SHAPES: dict[str, re.Pattern[str]] = {
    CHANNEL: re.compile(_JSON_STR + ',"dom":' + _JSON_STR + ',"fx":' + _JSON_STR),
    GAP_CHANNEL: re.compile(
        _JSON_STR + ',"spot":' + _JSON_STR + ',"perp":' + _JSON_STR
    ),
    PP_CHANNEL: re.compile(_JSON_STR + ',"a":' + _JSON_STR + ',"b":' + _JSON_STR),
}
# 값 자리의 -0.0 — 문자열은 그룹 1 로 통째 건너뛴다. dict 비교(파싱 경로)는 0.0 == -0.0 이라 같은 값으로 본다.
# spark 의 round(-0.0004, 3) 이 -0.0 을 만들고(실데이터 행의 1.5%), gap 의 funding 도 거래소가 "-0" 을 주면 -0.0 이다
_NEG_ZERO = re.compile("(" + _JSON_STR + r")|(?<=[:\[,])-0\.0(?=[,\]}]|\Z)")


def _keep_str(m: re.Match[str]) -> str:
    return m.group(1) or "0.0"


def _zero(cmp: str) -> str:
    """비교 글자의 값 -0.0 을 0.0 으로 — 0.0 ↔ -0.0 만 바뀐 행을 파싱 경로처럼 "안 바뀜" 으로 본다."""
    if not ("-0.0," in cmp or "-0.0]" in cmp or "-0.0}" in cmp or cmp.endswith("-0.0")):
        return cmp  # -0.012 같은 음수뿐이다
    return _NEG_ZERO.sub(_keep_str, cmp)


def split_table(text: str, table_id: str) -> tuple[str, str, list[str]] | None:
    """표 글 → (rows 앞, rows 뒤, 행 조각들). 조각은 `{"sym":` 와 끝 `}` 를 뗀 가운데. 게시기 글 모양이 아니면 None."""
    key_end = KEY_ENDS.get(table_id)
    if key_end is None or not (text.startswith('{"') and text.endswith("}")):
        return None
    # `\u` 이스케이프가 있으면 ensure_ascii=True 인코더 글일 수 있다 — 파싱해서 다시 쓴 바이트(한글 그대로)와 갈린다.
    # 한 글자 검사(표 1장 0.02ms)로 먼저 거른다 — 게시기 글엔 보통 백슬래시가 하나도 없다
    if "\\" in text and "\\u" in text:
        return None
    i = text.find(ROWS_OPEN)
    if i < 0:
        return None
    s = i + len(ROWS_OPEN)
    j = text.find(ROWS_CLOSE, s)
    if j < 0:
        return None
    head, tail = text[:i], text[j + 2 :]
    # 최상위에 `type`·`removed` 가 있으면 파싱 경로의 키 자리·값이 끼운 글과 달라진다(지금 표엔 없다)
    for name in ('"type":', '"removed":'):
        if name in head or name in tail:
            return None
    body = text[s:j]
    if not body:
        return head, tail, []
    if not (body.startswith(ROW_HEAD) and body.endswith("}")):
        return None
    parts = body[len(ROW_HEAD) : -1].split(ROW_SEP)
    k = parts[0].find(key_end)
    if k < 0 or KEY_SHAPES[table_id].fullmatch(parts[0], 0, k) is None:
        return None
    return head, tail, parts


def index_text(text: str, table_id: str) -> dict[str, str] | None:
    """표 글 → {키 글자: `age` 를 뺀 비교 글자} — `index_rows` 의 글 판. 게시기 글 모양이 아니면 None."""
    sp = split_table(text, table_id)
    if sp is None:
        return None
    parts = sp[2]
    key_end = KEY_ENDS[table_id]
    shape = KEY_SHAPES[table_id]
    out: dict[str, str] = {}
    for p in parts:
        k = p.find(key_end)
        a = p.find(AGE)
        # 표식 앞의 `,"` 가 둘 = 키 필드가 셋 — 필드가 끼면 경계가 늘어 걸린다. 문자열 안의 따옴표는 `\"` 라 `,"` 는
        # 키 경계와 쉼표로 끝나는 문자열 끝에만 생긴다. 둘이 아닌 드문 행만 꼴 정규식으로 다시 본다(행마다 정규식이면
        # 표 1장에 spreads 0.7ms·gap 1.7ms 가 든다)
        if k < 0 or a < 0:
            return None
        if p.count(',"', 0, k) != 2 and shape.fullmatch(p, 0, k) is None:
            return None
        b = p.find(",", a + 7)
        cmp = p[:a] + p[b:] if b >= 0 else p[:a]  # gap 은 age 가 마지막 키라 뒤가 없다
        if "-0.0" in cmp:
            cmp = _zero(cmp)
        out[p[:k]] = cmp
    if len(out) != len(parts):
        return None  # 같은 키 행이 둘 — 파싱 경로의 비교와 맞추기 어려운 모양이다(지금 표엔 없다)
    return out


def make_delta_text(
    prev: dict[str, str], text: str, table_id: str
) -> tuple[str, dict[str, str]] | None:
    """직전 글 인덱스와 새 표 글 → (delta 메시지, 새 글 인덱스) — `make_delta` 와 같은 바이트. 모양이 다르면 None."""
    sp = split_table(text, table_id)
    if sp is None:
        return None
    head, tail, parts = sp
    key_end = KEY_ENDS[table_id]
    shape = KEY_SHAPES[table_id]
    cur: dict[str, str] = {}
    changed: list[str] = []
    get = prev.get
    for p in parts:
        k = p.find(key_end)
        a = p.find(AGE)
        if k < 0 or a < 0:
            return None
        if p.count(',"', 0, k) != 2 and shape.fullmatch(p, 0, k) is None:
            return None  # 키 필드 셋의 꼴 — `index_text` 와 같은 검사
        b = p.find(",", a + 7)
        cmp = p[:a] + p[b:] if b >= 0 else p[:a]
        if "-0.0" in cmp:
            cmp = _zero(cmp)
        kt = p[:k]
        cur[kt] = cmp
        if get(kt) != cmp:
            changed.append(p)
    if len(cur) != len(parts):
        return None
    key = ROW_KEYS[table_id]
    try:
        # 사라진 키는 키 글자만 파싱해 행 키 함수로 — 파싱 경로와 같은 글, 직전 표의 행 순서
        removed = [key(json.loads(ROW_HEAD + kt + "}")) for kt in prev if kt not in cur]
        # rows 를 뺀 작은 JSON — 키 순서가 표 그대로라 `make_delta` 의 메타와 같은 글이 나온다
        meta = json.loads(head + tail)
    except (ValueError, KeyError, TypeError):
        return None
    rows = ROW_HEAD + ROW_SEP.join(changed) + "}" if changed else ""
    message = (
        _dumps({"type": "delta", **meta})[:-1]
        + ',"rows":['
        + rows
        + '],"removed":'
        + _dumps(removed)
        + "}"
    )
    return message, cur


def snapshot_text(text: str) -> str:
    """`make_snapshot(json.loads(text))` 와 같은 바이트 — `split_table` 이 받아 준 글에만 쓴다."""
    return '{"type":"snapshot",' + text[1:]


# --- 접속 하나 ---


class Connection:
    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=SEND_QUEUE_LIMIT)
        self._sender: asyncio.Task[None] | None = None
        self._closing = False

    def start(self) -> None:
        self._sender = asyncio.create_task(self._run_sender())

    def offer(self, message: bytes) -> None:
        """대기열에 넣는다 — 가득 차 있으면 느린 접속자로 보고 닫는다. 기다리지 않는다."""
        if self._closing:
            return
        try:
            self._queue.put_nowait(message)
        except asyncio.QueueFull:
            self.kick(CODE_SLOW)

    def kick(self, code: int) -> None:
        """다른 태스크(허브)에서 닫기 — 보내기 태스크를 멈추고 닫기 프레임은 별도 태스크로."""
        if self._closing:
            return
        self._closing = True
        asyncio.create_task(self.close(code))

    async def close(self, code: int) -> None:
        self._closing = True
        if self._sender is not None:
            self._sender.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._sender
            self._sender = None
        with contextlib.suppress(Exception):
            await self._ws.close(code)

    async def _run_sender(self) -> None:
        """대기열 순서대로 전송. HEARTBEAT_SEC 동안 보낼 게 없으면 heartbeat. 전송 실패면 끝."""
        while True:
            try:
                message = await asyncio.wait_for(self._queue.get(), HEARTBEAT_SEC)
            except TimeoutError:
                message = HEARTBEAT
            try:
                await self._ws.send_bytes(message)
            except Exception:
                return  # 끊긴 소켓 — 수신 쪽(라우터)이 detach 한다


class _Out:
    """나갈 프레임 하나와 받을 접속자 — 압축이 스레드에서 도는 동안 `frame` 이 None 이다."""

    __slots__ = ("frame", "job", "task", "to")

    def __init__(self, to: list[Connection], frame: bytes | None = None) -> None:
        self.frame = frame
        self.to = to
        self.job: asyncio.Future[bytes] | None = None
        # 스레드 쪽 작업 그 자체 — 버릴 때 루프 한 바퀴를 기다리지 않고 바로 취소한다(`_drop`)
        self.task: concurrent.futures.Future[bytes] | None = None


# --- 허브 ---


class SpreadsHub:
    """표 1장의 구독·diff·브로드캐스트 허브 — `table` 은 표 id(`spreads` 기본, 048 은 `gap`, 049 는 `pp`)."""

    def __init__(self, *, bus: RedisBus, table: str = CHANNEL) -> None:
        self._bus = bus
        self._table_id = table
        self._key = ROW_KEYS[table]
        # 보는 사람 흔적은 표마다 `<id>:want` — 다른 표의 접속으로 갱신되면 안 된다 (048 §3.4)
        self._conns: set[Connection] = set()
        self._text: str | None = None  # 직전 표 글 — 접속자가 있을 때만
        # 직전 표의 비교 형태 — 글 인덱스 {키 글자: 비교 글자} 또는(글 모양이 다를 때) 파싱 인덱스 {행 키: 행}
        self._index: dict | None = None
        self._text_index = False  # `_index` 가 글 인덱스인가
        # 지금 표의 snapshot 프레임 — 표 1장당 한 번만 압축한다. 표가 바뀌거나 버려지는 모든 자리에서 비운다:
        # 옛 표의 snapshot 위에 새 표 기준 delta 가 얹히면 행이 틀어진다 (§3.2). 압축 중이면 그 프레임을 같이 받는다
        self._snap: _Out | None = None
        # 나갈 프레임 줄 — 만든 순서대로 나간다. 앞 프레임이 압축 중이면 뒤 프레임은 다 됐어도 기다린다
        self._out: deque[_Out] = deque()
        self._gzip: ThreadPoolExecutor | None = (
            None  # 첫 큰 프레임 때 만든다 — 접속자가 없는 허브엔 스레드가 없다
        )
        self._shape_warned = -SHAPE_WARN_SEC
        self._task: asyncio.Task[None] | None = None
        self._wanted = (
            asyncio.Event()
        )  # 접속자가 생기면 켠다 — 구독 태스크가 이것을 기다렸다 구독한다
        self._idle_since: float | None = None  # 마지막 접속자가 떠난 시각(monotonic)
        self._loaded = asyncio.Event()  # 첫 접속자의 latest 읽기가 끝났는가
        self._loaded.set()

    @property
    def connections(self) -> int:
        return len(self._conns)

    @property
    def table_id(self) -> str:
        return self._table_id

    async def attach(self, ws: WebSocket) -> Connection:
        """접속 등록 → 첫 접속자면 `spreads:latest` 로 시작 표 → snapshot 또는 waiting."""
        conn = Connection(ws)
        conn.start()
        first = not self._conns
        self._conns.add(conn)
        self._idle_since = None
        self._wanted.set()  # 구독이 닫혀 있으면 구독 태스크가 연다 — latest 읽기와 겹쳐 돈다
        if first:
            self._loaded = asyncio.Event()
            try:
                await self._load_latest()
            finally:
                self._loaded.set()
            # 보는 사람이 있다는 흔적을 지금 1회 — spreads 는 수집이 읽지 않고(017 §3.1), gap 은 수집이 이 키를 보고
            # 표를 만들기 시작한다(048 §3.3) — 그래서 첫 접속자는 waiting 뒤 1~2초 안에 snapshot 을 받는다
            await self._refresh_want()
        else:
            # 첫 접속자가 latest 를 읽는 중이면 끝날 때까지 기다린다 — 그 사이 waiting 을 받은 접속자에게
            # latest 기준 delta 가 이어지면 snapshot 없이 바뀐 행만 받는다 (§3.2, 배포 직후 전원 재접속)
            await self._loaded.wait()
        if self._text is not None:
            snap = self._snap
            if snap is None:
                # 이 표를 처음 받는 접속자 — 여기서 한 번 압축하고, 같은 표 동안 붙는 접속자는 이 프레임을 받는다
                self._snap = self._send(self._snapshot(self._text), [conn])
            elif snap.frame is None:
                snap.to.append(
                    conn
                )  # 아직 압축 중 — 끝나면 같은 바이트를 같은 자리에서 받는다
            else:
                self._put(snap.frame, [conn])
        else:
            self._put(WAITING, [conn])
        return conn

    def detach(self, conn: Connection) -> None:
        self._conns.discard(conn)
        if not self._conns:
            self._drop()  # 0명 — 상태와 나갈 프레임을 버린다 (§3.2)
            self._idle_since = time.monotonic()

    def on_table(self, text: str) -> None:
        """새 표 1장 — 접속자가 없으면 파싱조차 안 한다. 있으면 diff 1회·gzip 1회, 전원에게 같은 바이트.

        표 상태는 여기서 바로 바꾸고 압축만 스레드로 보낸다 — 압축을 기다리는 사이 붙는 접속자는 이 표의 snapshot 을 받고,
        이 delta 는 받지 않는다(받을 접속자를 지금 정한다). 그 접속자에게도 다음 표부터는 delta 가 이어진다.
        """
        if not self._conns:
            return
        if self._index is None:
            # waiting 중이던 접속자들의 첫 표 — 이 프레임이 곧 이 표의 snapshot 이라 그대로 칸에 둔다
            index, by_text, table = self._index_of(text)
            message = self._snapshot(text, by_text, table)
            first = True
        else:
            message, index, by_text = self._delta(text)
            first = False
        self._text, self._index, self._text_index = text, index, by_text
        out = self._send(message, list(self._conns))
        self._snap = out if first else None  # 새 표(delta) — 다음 접속자가 한 번 만든다

    # --- 표 글 → 메시지 ---

    def _index_of(self, text: str) -> tuple[dict, bool, dict | None]:
        """(인덱스, 글 인덱스인가, 파싱한 표) — 글 모양이 다를 때만 파싱한다."""
        index = index_text(text, self._table_id)
        if index is not None:
            return index, True, None
        self._warn_shape(text)
        table = json.loads(text)
        return index_rows(table, self._key), False, table

    def _snapshot(
        self, text: str, by_text: bool | None = None, table: dict | None = None
    ) -> str:
        """snapshot 메시지 — 글 인덱스가 선 표는 앞에 `type` 만 끼우고, 아니면 파싱해서 다시 쓴다."""
        if by_text is None:
            by_text = self._text_index
        if by_text:
            return snapshot_text(text)
        return make_snapshot(table if table is not None else json.loads(text))

    def _delta(self, text: str) -> tuple[str, dict, bool]:
        """(delta 메시지, 새 인덱스, 글 인덱스인가). 글 경로가 안 되는 표만 파싱하고, 다음 표는 다시 글 경로부터 본다."""
        if self._text_index:
            assert self._index is not None
            done = make_delta_text(self._index, text, self._table_id)
            if done is not None:
                return done[0], done[1], True
            # 이 표가 글 모양에서 벗어났다 — 직전 표 글을 이번 한 번만 파싱해 파싱 경로로
            assert self._text is not None
            prev = index_rows(json.loads(self._text), self._key)
        else:
            assert self._index is not None
            prev = self._index
        message, cur = make_delta(prev, json.loads(text), self._key)
        index = index_text(text, self._table_id)
        if index is not None:
            return message, index, True  # 모양이 돌아왔다 — 다음 표부터 글 경로
        self._warn_shape(text)
        return message, cur, False

    def _warn_shape(self, text: str) -> None:
        now = time.monotonic()
        if now - self._shape_warned >= SHAPE_WARN_SEC:
            self._shape_warned = now
            logger.warning(
                "%s 표 글이 게시기 모양이 아니다 — 파싱해서 diff 한다(바이트는 같고 느리다): %r",
                self._table_id,
                text[:80],
            )

    # --- 나가는 프레임 ---

    def _send(self, message: str, to: list[Connection]) -> _Out:
        """메시지를 압축해 줄에 세운다 — 큰 메시지는 허브 전용 스레드에서, 작은 것은 여기서 바로."""
        if len(message) < THREAD_PACK_MIN:
            out = _Out(to, pack(message))
        else:
            if self._gzip is None:
                self._gzip = ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix=f"{self._table_id}-gzip"
                )
            out = _Out(to)
            # pack 은 부르는 시점의 모듈 전역 — 스레드 1개라 끝나는 순서 = 넣은 순서. 스레드 쪽 작업(task)을 따로 들고
            # 루프 쪽 future(job)로 감싼다 — run_in_executor 와 같은 짝인데, 버릴 때 task 를 그 자리에서 취소하려고
            task = self._gzip.submit(pack, message)
            job = asyncio.wrap_future(task)
            job.add_done_callback(functools.partial(self._packed, out))
            out.job, out.task = job, task
        self._out.append(out)
        self._flush()
        return out

    def _put(self, frame: bytes, to: list[Connection]) -> None:
        """이미 압축된 프레임(waiting·snapshot 칸) — 앞에 압축 중인 프레임이 있으면 그 뒤에 선다."""
        self._out.append(_Out(to, frame))
        self._flush()

    def _packed(self, out: _Out, job: asyncio.Future[bytes]) -> None:
        if job.cancelled():
            return  # 0명·종료 때 버린 프레임
        exc = job.exception()
        if exc is not None:
            # 받을 접속자들의 표가 이 프레임만큼 비고, 줄에 남은 프레임은 이 프레임을 전제로 만든 것이다 — 상태와 줄을
            # 버려 다음 표를 전원에게 snapshot 으로 보낸다(그 사이 붙는 접속자는 waiting)
            logger.warning(
                "%s 프레임 압축 실패 — 다음 표를 snapshot 으로: %r", self._table_id, exc
            )
            self._drop()
            return
        out.frame = job.result()
        self._flush()

    def _flush(self) -> None:
        """줄 앞에서부터 압축이 끝난 프레임을 내보낸다 — 받을 접속자는 프레임을 만들 때 정한 그대로다.

        그 사이 떠난 접속자에게도 넣는다(동기로 압축하던 때도 넣었다 — 닫힌 소켓이면 보내기 태스크가 이미 끝나 있다).
        0명이 되면 줄 자체를 버린다(`_drop`).
        """
        queue = self._out
        while queue and queue[0].frame is not None:
            out = queue.popleft()
            frame, to = out.frame, out.to
            out.to = []  # 칸(`_snap`)이 이 프레임을 계속 들고 있어도 접속 목록은 놓는다
            for conn in to:
                conn.offer(frame)

    def _drop(self) -> None:
        """상태·snapshot 칸·나갈 프레임을 버린다 — 0명·종료. 아직 시작 안 한 압축은 스레드에서 빼고, 도는 것은 결과를 버린다."""
        self._text = self._index = None
        self._text_index = False
        self._snap = None
        # 뒤 프레임부터 취소한다 — 앞 작업을 취소하는 동안(future 락) 스레드가 깨어나 그 작업을 건너뛰고 다음 작업을
        # 잡을 수 있어서, 앞에서부터 취소하면 아직 시작 안 한 뒤 프레임이 스레드에서 돌 수 있다(2026-10-13 CI). 스레드 쪽
        # 작업(task)도 그 자리에서 취소한다 — 루프 쪽 future 취소는 다음 루프 회차에야 스레드 작업까지 닿는다.
        # 이미 도는 압축은 결과를 버린다(`_packed` 가 취소된 future 를 건너뛴다)
        for out in reversed(self._out):
            if out.task is not None:
                out.task.cancel()
            if out.job is not None:
                out.job.cancel()
        self._out.clear()

    async def _load_latest(self) -> None:
        try:
            text = await self._bus.latest(self._table_id)
        except Exception as exc:
            logger.warning(
                "%s:latest 읽기 실패 — waiting 으로 시작: %r", self._table_id, exc
            )
            return
        if text is None or not self._conns:
            return  # 읽는 사이 전원이 떠났으면 상태를 들지 않는다 — 0명은 상태가 없다
        index, by_text, _ = self._index_of(text)
        self._text, self._index, self._text_index = text, index, by_text
        self._snap = None

    async def _refresh_want(self) -> None:
        if not self._conns:
            return
        try:
            await self._bus.want(self._table_id)
        except Exception as exc:
            logger.warning("%s:want 갱신 실패: %r", self._table_id, exc)

    def start(self) -> None:
        self._task = asyncio.create_task(self.run(), name=f"{self._table_id}_hub")

    async def run(self) -> None:
        """구독 태스크 하나 — 접속자가 생기면 구독해 채널 수신 + 5초마다 want 갱신, 0명 30초면 구독을 닫고
        다음 접속자를 기다린다. 끊기면 1→2→…→30초 백오프."""
        backoff = BACKOFF_MIN_SEC
        while True:
            await self._wanted.wait()
            try:
                sub = await self._bus.subscribe(self._table_id)
            except Exception as exc:
                logger.warning(
                    "Redis 구독 연결 실패 — %.0f초 뒤 재시도: %r", backoff, exc
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX_SEC)
                continue
            next_want = time.monotonic()
            try:
                while not self._idle_expired():
                    text = await sub.get(SUB_POLL_SEC)
                    # 백오프는 구독이 실제로 받는 것을 본 뒤에야 되돌린다 — SUBSCRIBE 거부(Redis 메모리 상한 등)는
                    # 첫 get 에서야 드러나서, 연결 직후에 되돌리면 매초 재연결·경고를 되풀이한다
                    backoff = BACKOFF_MIN_SEC
                    if text is not None:
                        self.on_table(text)
                    if time.monotonic() >= next_want:
                        await self._refresh_want()
                        next_want = time.monotonic() + WANT_REFRESH_SEC
            except asyncio.CancelledError:
                await sub.aclose()
                raise
            except Exception as exc:
                logger.warning("Redis 구독 끊김 — %.0f초 뒤 재연결: %r", backoff, exc)
                with contextlib.suppress(Exception):
                    await sub.aclose()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX_SEC)
                continue
            # 0명 30초 — 구독을 닫는다. 닫는 사이에 누가 붙으면 attach 가 다시 켜 두어 곧바로 다시 구독한다
            self._wanted.clear()
            with contextlib.suppress(Exception):
                await sub.aclose()

    def _idle_expired(self) -> bool:
        return (
            not self._conns
            and self._idle_since is not None
            and time.monotonic() - self._idle_since >= IDLE_UNSUBSCRIBE_SEC
        )

    async def aclose(self) -> None:
        """구독을 멈추고 접속자 전원을 1001 로 닫는다. 압축 스레드는 기다리지 않고 닫는다(도는 압축의 결과는 버린다)."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        conns, self._conns = list(self._conns), set()
        await asyncio.gather(*(c.close(CODE_GOING_AWAY) for c in conns))
        self._drop()
        if self._gzip is not None:
            self._gzip.shutdown(wait=False, cancel_futures=True)
            self._gzip = None
