"""요소 열쇠 계약 — 대시보드 소스·정적 페이지의 data-el 이 꼴에 맞고 화면 안에서 겹치지 않으며 스펙 061 §7 '요소 목록' 과 같다.

065 의 이름표가 쓰는 목록이라 화면마다 그대로 적어 둔다. 대시보드(JSX)는 소스에서 열쇠 글자를 뽑아 화면(공통 + 탭 파일)마다
대조한다 — 영역은 소스 차례로 정할 수 없어 브라우저 확인(설계 세션)이고, 대신 열쇠가 화면 안에서 하나뿐인지 본다(그러면
(영역, 열쇠)도 겹치지 않는다). `<접두>:*` 는 템플릿으로 만든 열쇠 모임 — 아래에서 그 값 목록이 꼴에 맞는지 따로 본다.
정적 페이지는 HTML 을 읽어 (영역, 열쇠) 차례 그대로 대조한다(스크립트가 그리는 카드·행은 열쇠만).
"""

import re
from collections import Counter
from html.parser import HTMLParser

from tests.test_attention import PUBLIC, WEB, _tab_ids

# 061 §3.1 — data-el 값은 영문 소문자·숫자·-·: (행 row:<심볼> 만 심볼 대문자 그대로)
VALUE = re.compile(r"[a-z0-9][a-z0-9-]*(?::[a-z0-9-]+)*")
SRC = WEB / "src"
COMMON = [
    "tab:*",
    "privacy",
    "consent-settings",
]  # 처리방침·설정 링크는 영역 밖(052) — 세지 않는다
DASHBOARD = {
    "spread": (
        ["features/spreads/Tab.tsx"],
        [
            "row:*",
            *(f"sort:{c}" for c in ("sym", "chg", "price", "usd", "fxex", "domex")),
            *(f"sort:{c}" for c in ("val", "io", "net")),
            "search",
            *("dom:all", "dom:upbit", "dom:bithumb", "threshold", "only-over"),
            *("only-io", "only-net", "view:kimp", "view:rev", "fx:all", "fx:*"),
        ],
    ),
    "history": (
        ["features/history/Tab.tsx", "features/history/Chart.tsx"],
        [
            *(f"sort:{c}" for c in ("ongoingsince", "cnt", "maxdur", "avgdur")),
            *(f"sort:{c}" for c in ("maxpct", "avgpct", "net", "last", "sym")),
            *("dir:*", "period:*", "dom:all", "dom:upbit", "dom:bithumb"),
            *("fx:all", "fx:*", "search", "row:*"),
            *("chart-dom:all", "chart-dom:*", "interval:*", "fold:*", "card:*"),
        ],
    ),
    "gap": (
        ["features/gap/Tab.tsx"],
        [
            *("sort:sym", "sort:price", "sort:gap", "sort:funding", "search"),
            *("mode:entry", "mode:exit", "threshold", "only-over"),
        ],
    ),
    "pp": (
        ["features/pp/Tab.tsx"],
        [
            *("sort:sym", "sort:gap", "sort:fund", "search"),
            *("threshold-price", "threshold-funding", "only-over"),
        ],
    ),
    "health": (["features/health/Tab.tsx"], []),
    "flow": (
        ["features/flow/Tab.tsx"],
        [
            *("window:1h", "window:6h", "window:24h", "dir:all", "dir:in"),
            *("dir:out", "row:*", "tx", "search"),
        ],
    ),
}
# 정적 페이지 — 문서 차례의 (영역, 열쇠). 자동 열쇠(링크 경로·글자)가 영역 안에서 겹치는 링크와 질문에만 단다
STATIC = {
    "landing": [
        *(("top", f"nav:{s}") for s in ("kimp", "method", "faq")),
        ("hero", "app:history"),
        *(("faq", f"faq:{q}") for q in ("why-differ", "why-1000", "rate", "blocked")),
        *(("faq", f"faq:{q}") for q in ("cost", "reverse-term", "chart")),
        ("foot", "app:history"),
        *(("foot", f"nav:{s}") for s in ("kimp", "method", "faq")),
        ("foot", "consent-settings"),
    ],
    "privacy": [],
    "kimp-chart": [
        *(("coins", f"coin:{c}") for c in ("btc", "eth", "xrp", "sol", "doge", "all")),
        *(("faq", f"faq:{q}") for q in ("realtime", "vs-price", "apps", "fees")),
        ("foot", "nav:kimp"),
    ],
    "kimp-history": [
        *(("faq", f"faq:{q}") for q in ("range", "max-vs-live", "profit")),
        ("foot", "nav:kimp"),
    ],
}
# 스크립트가 그리는 것 — 랜딩 실시간 카드(hero, 1위~5위)·사건 표 행(events), 검색어 페이지 사건 표 행(live)
SCRIPTED = {"landing": ["live:1", "live:*", "row:*"], "kimp-history": ["row:*"]}

# 템플릿은 data-el={`…`}·el: `…`·인자(, `…`) 자리만 — 차트 시리즈 이름(key: `dom:${…}`) 같은 다른 글자는 뺀다
_LITERAL = re.compile(
    r"""data-el="([^"]*)"|\bel="([^"]*)"|(?:(?<=data-el=\{)|(?<=\bel: )|(?<=, ))`([a-z][a-z0-9-]*:)\$\{"""
    r"""|'([a-z][a-z0-9-]*:[a-z0-9:-]+)'"""
)


def _keys(text: str) -> list[str]:
    """소스의 data-el 글자 — JSX 속성·el 속성·작은따옴표 속 콜론 글자·`<접두>:${…}` 템플릿(→ `<접두>:*`)."""
    out = []
    for attr, prop, family, quoted in _LITERAL.findall(text):
        value = attr or prop or quoted or f"{family}*"
        if value != "kt:tab":  # 셸이 내는 탭 이벤트 이름(052) — 열쇠가 아니다
            out.append(value)
    return out


def _source(*rels: str) -> list[str]:
    return [key for rel in rels for key in _keys((SRC / rel).read_text("utf-8"))]


def test_dashboard_keys_match_the_list_and_are_unique_per_screen() -> None:
    assert sorted(_source("App.tsx")) == sorted(COMMON)
    for tab, (files, expected) in DASHBOARD.items():
        found = _source(*files)
        literals = [k for k in found if not k.endswith("*")]
        dup = [k for k, n in Counter(literals + _source("App.tsx")).items() if n > 1]
        assert not dup, (tab, dup)
        assert set(found) == set(expected) and len(expected) == len(set(expected)), (
            tab,
            set(found) ^ set(expected),
        )
        for key in found:
            assert VALUE.fullmatch(key.removesuffix("*").rstrip(":")), (tab, key)
    # 공유 조각은 받은 값을 그대로 단다 — 열쇠 글자를 박지 않는다
    assert _source("shared/ui.tsx") == []
    assert (
        len(
            re.findall(
                r"data-el=\{(?:o\.el|el)\}", (SRC / "shared/ui.tsx").read_text("utf-8")
            )
        )
        == 4
    )


def _list(rel: str, name: str) -> list[str]:
    found = re.search(
        rf"const {name}\b[^=]*= \[([^\]]*)\]", (SRC / rel).read_text("utf-8")
    )
    assert found, (rel, name)
    return re.findall(r"'([^']+)'", found.group(1))


def test_template_families_expand_to_lowercase_values() -> None:
    families = {
        "tab": _tab_ids(),
        # 스프레드의 해외 거래소 체크 상자 — 표시명을 소문자·영숫자로(Gate.io → gateio)
        "fx (spread)": [
            re.sub(r"[^a-z0-9]", "", fx.lower())
            for fx in _list("shared/mock.ts", "FX_EXS")
        ],
        "period": _list("features/history/Tab.tsx", "PERS"),
        "interval": _list("features/history/rollup.ts", "INTERVALS"),
        "chart-dom": _list("features/history/Chart.tsx", "DOMS"),
        "fx·fold·card": re.findall(
            r"\{ id: '([^']+)'",
            (SRC / "features/history/candles.ts").read_text("utf-8"),
        ),
        "dir": ["kimp", "reverse"],
    }
    for name, values in families.items():
        assert values and len(values) == len(set(values)), name
        assert all(VALUE.fullmatch(v) for v in values), (name, values)
    assert families["fx (spread)"] == [
        "binance", "bybit", "bitget", "okx", "mexc", "gateio", "hyperliquid",
    ]  # fmt: skip


class _Elements(HTMLParser):
    """data-el 요소를 문서 차례로 (그것을 품은 영역, 열쇠) — 스크립트 안은 보지 않는다."""

    VOID = {"meta", "link", "img", "br", "hr", "input", "source", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[str | None, str]] = []
        self._stack: list[tuple[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        got = dict(attrs)
        area = got.get("data-area")
        if area is None and self._stack:
            area = next((a for _, a in reversed(self._stack) if a is not None), None)
        if got.get("data-el") is not None:
            self.found.append((area, got["data-el"]))
        if tag not in self.VOID:
            self._stack.append((tag, got.get("data-area")))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                del self._stack[i:]
                return


def test_static_pages_carry_the_listed_keys_inside_areas() -> None:
    files = {"landing": "landing.html", "privacy": "privacy.html"}
    files |= {p: f"{p}.html" for p in ("kimp-chart", "kimp-history")}
    for page, file in files.items():
        html = (PUBLIC / file).read_text("utf-8")
        scripts = "".join(re.findall(r"<script\b[^>]*>(.*?)</script>", html, re.S))
        parser = _Elements()
        parser.feed(re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.S))
        assert parser.found == STATIC[page], page
        assert len(set(parser.found)) == len(parser.found), page
        assert all(VALUE.fullmatch(k) for _, k in parser.found), page
        drawn = [
            f"{m[0]}:*" if m[1] else m[0]
            for m in re.findall(r'data-el="([a-z0-9:-]+?)(?:(:)\$\{[^"]*)?"', scripts)
        ]
        assert drawn == SCRIPTED.get(page, []), page
    # 예전 판 사본·404 에는 없다(세는 스크립트가 없다)
    for path in (*PUBLIC.glob("privacy-*.html"), PUBLIC / "404.html"):
        assert "data-el" not in path.read_text("utf-8"), path.name
