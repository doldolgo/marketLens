# /// script
# requires-python = ">=3.11"
# dependencies = ["fonttools[woff]==4.60.1"]
# ///
"""랜딩 전용 글꼴 — Pretendard Variable 을 landing.html 이 쓰는 글자만 남겨 woff2 하나로 자른다 (스펙 022 §3.4).

    uv run web/scripts/subset-landing-font.py [원본.woff2]

- 입력: jsDelivr 의 pretendard@1.3.9 PretendardVariable.woff2(인자가 없으면 임시 폴더에 받는다 — 공개 폴더에 남기지 않는다).
  굵기 축은 400~700 만 남긴다.
- 글자: landing.html 에서 <style>·HTML 주석·JS 한 줄 주석을 뺀 나머지의 모든 글자 + 인쇄 가능한 ASCII 전부.
  스크립트가 그리는 글(경로 카드·사건 표)도 이 파일 안의 문자열이라 함께 들어간다. API 가 주는 한글(망 이름 등)은
  빠질 수 있다 — 그 글자는 font-family 목록 뒤의 시스템 글꼴로 보인다.
- 레이아웃 기능: fontTools 기본(kern·locl·calt·한글 자모 등) + tnum(페이지가 켜는 tabular-nums). 페이지가 쓰지 않는
  문체 대체(ss·cv·frac 등)의 글리프는 담지 않는다.
- 이름: Pretendard 는 OFL 1.1 에 Reserved Font Name 이 걸려 있어, 자른 파일(수정본)은 다른 이름을 써야 한다 →
  "KimpTrack Sans". 저작권·상표·제작자·라이선스 표기는 그대로 두고 OFL.txt 를 옆에 둔다.
- 출력: web/public/landing/fonts/kimptrack-sans-<내용 해시 8자>.woff2(옛 파일은 지운다) — 이름이 내용을 따라 바뀌므로
  글을 고친 배포 뒤 브라우저가 옛 글꼴을 쓰지 않는다. landing.html 의 preload·@font-face 주소도 새 이름으로 고친다.
  글자 목록은 web/scripts/landing-font-glyphs.txt(공개하지 않는다 — server/tests 가 landing.html 의 글자가 모두 여기
  있는지 본다).
"""

from __future__ import annotations

import hashlib
import io
import re
import sys
import tempfile
import urllib.request
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = Path(__file__).resolve().parents[2]
LANDING = ROOT / "web/public/landing.html"
OUT_DIR = ROOT / "web/public/landing/fonts"
GLYPHS = ROOT / "web/scripts/landing-font-glyphs.txt"
SOURCE_URL = "https://cdn.jsdelivr.net/npm/pretendard@1.3.9/dist/web/variable/woff2/PretendardVariable.woff2"
FAMILY = "KimpTrack Sans"
PS_NAME = "KimpTrackSans"
FONT_REF = re.compile(r"landing/fonts/kimptrack-sans(?:-[0-9a-f]{8})?\.woff2")


def page_text(html: str) -> str:
    """화면에 나올 수 있는 글 — 스타일·주석을 뺀다 (server/tests/test_landing_seo.py 와 같은 규칙)."""
    html = re.sub(r"<style\b.*?</style>", "", html, flags=re.S)
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    return re.sub(r"(^|\s)//[^\n]*", r"\1", html)


def rename(font: TTFont) -> None:
    names = font["name"]
    for rec in list(names.names):
        if rec.nameID in (1, 16):
            names.setName(FAMILY, rec.nameID, rec.platformID, rec.platEncID, rec.langID)
        elif rec.nameID == 4:
            names.setName(f"{FAMILY} Variable", 4, rec.platformID, rec.platEncID, rec.langID)
        elif rec.nameID == 6:
            names.setName(f"{PS_NAME}-Variable", 6, rec.platformID, rec.platEncID, rec.langID)
        elif rec.nameID == 3:
            names.setName(f"{PS_NAME};landing-subset", 3, rec.platformID, rec.platEncID, rec.langID)
        elif rec.nameID == 25:  # Variations PostScript name prefix
            names.setName(PS_NAME, 25, rec.platformID, rec.platEncID, rec.langID)
        elif rec.nameID >= 256:  # fvar 인스턴스 이름(PretendardVariable-Bold 등)
            text = rec.toUnicode().replace("PretendardVariable", PS_NAME).replace("Pretendard", FAMILY)
            names.setName(text, rec.nameID, rec.platformID, rec.platEncID, rec.langID)
    # 저작권(0)·상표(7)·제작사·디자이너·설명(8~12)·라이선스(13·14)는 원 저작자 표기라 그대로 둔다 — 글꼴 이름으로 쓰이는 칸만 바꾼다
    left = [
        r.toUnicode()
        for r in names.names
        if "Pretendard" in r.toUnicode() and r.nameID not in (0, 7, 8, 9, 10, 11, 12, 13, 14)
    ]
    if left:
        sys.exit(f"예약 이름이 남았다: {left}")


def build(src: Path) -> None:
    html = LANDING.read_text("utf-8")
    chars = set(page_text(html)) | {chr(c) for c in range(0x20, 0x7F)}
    chars -= {"\n", "\r", "\t"}

    # 굵기 축을 자른 뒤 한 번 저장해 다시 읽어 표를 정리하고(그대로 자르면 subset 이 지워진 글리프를 찾다 멈춘다), 글자를 줄인다
    font = instancer.instantiateVariableFont(TTFont(src), {"wght": (400, 700)})
    buf = io.BytesIO()
    font.save(buf)
    buf.seek(0)
    font = TTFont(buf)
    options = subset.Options()
    options.flavor = "woff2"
    options.layout_features = [*subset.Options().layout_features, "tnum"]
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.notdef_outline = True
    sub = subset.Subsetter(options)
    sub.populate(unicodes=[ord(c) for c in chars])
    sub.subset(font)
    rename(font)

    out = io.BytesIO()
    font.flavor = "woff2"
    font.save(out)
    data = out.getvalue()
    name = f"kimptrack-sans-{hashlib.sha256(data).hexdigest()[:8]}.woff2"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in OUT_DIR.glob("kimptrack-sans*.woff2"):
        if old.name != name:
            old.unlink()
    (OUT_DIR / name).write_bytes(data)
    refs = FONT_REF.findall(html)
    if len(refs) != 2:  # preload 하나 + @font-face 하나
        sys.exit(f"landing.html 의 글꼴 주소가 둘이 아니다: {refs}")
    LANDING.write_text(FONT_REF.sub(f"landing/fonts/{name}", html), "utf-8")

    cmap = font.getBestCmap()
    kept = sorted(c for c in chars if ord(c) in cmap and ord(c) > 0x7E)
    missing = sorted(c for c in chars if ord(c) not in cmap)
    GLYPHS.write_text("".join(kept) + "\n", "utf-8")
    print(f"landing/fonts/{name} {len(data):,}B · 글자 {len(kept)}개(ASCII 제외) · 원본에 없는 글자 {''.join(missing)!r}")


def main() -> None:
    if len(sys.argv) > 1:
        build(Path(sys.argv[1]))
        return
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "PretendardVariable.woff2"
        urllib.request.urlretrieve(SOURCE_URL, src)
        build(src)


if __name__ == "__main__":
    main()
