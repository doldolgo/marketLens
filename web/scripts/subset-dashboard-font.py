# /// script
# requires-python = ">=3.11"
# dependencies = ["fonttools[woff]==4.60.1"]
# ///
"""대시보드 글꼴 — Inter 를 Google Fonts 가 주던 라틴 범위 + ₩ 만 남겨 woff2 하나로 자른다 (스펙 002 §3.2).

    uv run web/scripts/subset-dashboard-font.py [원본.ttf]

- 입력: google/fonts 레포의 `ofl/inter/Inter[opsz,wght].ttf`(커밋을 박은 jsDelivr 주소 — Google Fonts 가 주던 판과 같은
  Version 4.001;git-66647c0bb). 인자가 없으면 임시 폴더에 받는다(원본은 레포에 남기지 않는다).
- 축: opsz 는 14 로 고정(Google Fonts 가 wght 만 청한 주소에 주던 값), 굵기 축은 400~700 만 남긴다(화면이 쓰는 굵기).
- 글자: Google Fonts css2 의 `latin` unicode-range 그대로 + ₩(U+20A9 — 헤더 KPI·스프레드 표 가격. 예전엔 이 한 글자 때문에
  latin-ext 파일을 더 받았다). 한글은 Inter 에 없어 지금처럼 font-family 목록 뒤의 시스템 글꼴로 보이고, 화살표·삼각형처럼
  예전에도 Inter 로 그리지 않던 글자도 담지 않는다 — 보이는 모양이 바뀌지 않게.
- 레이아웃 기능: fontTools 기본(kern·locl·calt·frac 등) + tnum(화면이 켜는 tabular-nums).
- 이름: Inter 는 OFL 1.1 이고 Reserved Font Name 이 없어 자른 파일도 이름 "Inter" 를 그대로 쓴다. 저작권·라이선스 표기도
  그대로 두고 같은 커밋의 OFL.txt 를 옆에 둔다.
- 출력: web/src/shared/fonts/inter-latin.woff2 + OFL.txt — index.css 의 @font-face 와 index.html 의 preload 가 이 파일을
  가리키고, 빌드가 내용 해시를 붙여 /app/assets/ 로 낸다(1년 immutable 캐시 — 007).
"""

from __future__ import annotations

import io
import sys
import tempfile
import urllib.request
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "web/src/shared/fonts"
OUT = OUT_DIR / "inter-latin.woff2"
COMMIT = "e1d6480102fed30739fead0faee463101f892c8f"  # google/fonts — "Inter: Version 4.001;git-66647c0bb added"
BASE = f"https://cdn.jsdelivr.net/gh/google/fonts@{COMMIT}/ofl/inter"
SOURCE_URL = f"{BASE}/Inter%5Bopsz,wght%5D.ttf"
LICENSE_URL = f"{BASE}/OFL.txt"
# Google Fonts css2 의 /* latin */ unicode-range (2026-10-23 받은 값) + ₩
LATIN = (
    "U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, "
    "U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD"
)
EXTRA = [0x20A9]


def unicodes() -> list[int]:
    out: list[int] = []
    for part in LATIN.split(","):
        lo, _, hi = part.strip().removeprefix("U+").partition("-")
        out.extend(range(int(lo, 16), int(hi or lo, 16) + 1))
    return out + EXTRA


def build(src: Path) -> None:
    # 축을 자른 뒤 한 번 저장해 다시 읽어 표를 정리하고(그대로 자르면 subset 이 지워진 글리프를 찾다 멈춘다), 글자를 줄인다
    # 저장 시각은 원본 값 그대로 둔다(recalcTimestamp=False) — 다시 돌려도 같은 바이트가 나와 빌드 해시가 바뀌지 않는다
    font = instancer.instantiateVariableFont(TTFont(src, recalcTimestamp=False), {"opsz": 14, "wght": (400, 700)})
    buf = io.BytesIO()
    font.save(buf)
    buf.seek(0)
    font = TTFont(buf, recalcTimestamp=False)
    options = subset.Options()
    options.flavor = "woff2"
    options.layout_features = [*subset.Options().layout_features, "tnum"]
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.notdef_outline = True
    sub = subset.Subsetter(options)
    sub.populate(unicodes=unicodes())
    sub.subset(font)

    out = io.BytesIO()
    font.flavor = "woff2"
    font.save(out)
    data = out.getvalue()
    OUT.write_bytes(data)
    cmap = font.getBestCmap()
    missing = [f"U+{c:04X}" for c in EXTRA if c not in cmap]
    if missing:
        sys.exit(f"원본에 없는 글자: {missing}")
    print(f"{OUT.relative_to(ROOT)} {len(data):,}B · 글자 {len(cmap)}개 · 축 {[a.axisTag for a in font['fvar'].axes]}")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(LICENSE_URL, OUT_DIR / "OFL.txt")
    with tempfile.TemporaryDirectory() as tmp:
        if len(sys.argv) > 1:
            build(Path(sys.argv[1]))
            return
        src = Path(tmp) / "Inter.ttf"
        urllib.request.urlretrieve(SOURCE_URL, src)
        build(src)


if __name__ == "__main__":
    main()
