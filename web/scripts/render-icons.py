# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow==12.0.0"]
# ///
"""아이콘 PNG·ICO — web/public/favicon.svg 한 장에서 나머지 아이콘 넷을 다시 만든다 (스펙 022 §3.6).

    uv run web/scripts/render-icons.py

- 헤드리스 크롬으로 SVG 를 1024px 로 찍고(투명 바탕), Pillow LANCZOS 로 줄인다 — 작은 크기를 직접 찍는 것보다 가장자리가 고르다.
- logo-512.png·icon-192.png 는 favicon.svg 그대로(둥근 사각형 밖은 투명).
- apple-touch-icon.png(180) 는 모서리를 둥글리지 않은 꽉 찬 사각형·불투명 RGB — iOS 가 제 모양으로 깎는다.
- favicon.ico 는 16·32·48 세 장.
- 마크를 바꾸려면 favicon.svg 만 고치고 이 스크립트를 다시 돌린다. 머리줄 마크(랜딩·처리방침·검색어 페이지·대시보드·관리자)와
  미리보기 그림(og-image.html)은 같은 좌표를 손으로 옮겨 둔 것이라 함께 고친다. 페이지의 아이콘 주소 `?v=` 도 하나 올린다 —
  브라우저는 같은 주소의 탭 아이콘을 다시 받지 않는다(test_landing_seo 의 ICON_VERSION).
"""

from __future__ import annotations

import base64
import io
import os
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

PUBLIC = Path(__file__).resolve().parents[1] / "public"
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
BIG = 1024


def shoot(svg: str, tmp: Path) -> Image.Image:
    data = base64.b64encode(svg.encode()).decode()
    page = tmp / "icon.html"
    page.write_text(
        f'<!doctype html><html><body style="margin:0;background:transparent">'
        f'<img src="data:image/svg+xml;base64,{data}" width="{BIG}" height="{BIG}" style="display:block">'
        f"</body></html>"
    )
    out = tmp / "icon.png"
    subprocess.run(
        [
            CHROME,
            "--headless",
            "--hide-scrollbars",
            "--default-background-color=00000000",
            f"--window-size={BIG},{BIG}",
            f"--screenshot={out}",
            page.as_uri(),
        ],
        check=True,
        capture_output=True,
    )
    with Image.open(out) as img:
        return img.convert("RGBA").copy()


def small(img: Image.Image, n: int) -> Image.Image:
    return img.resize((n, n), Image.Resampling.LANCZOS)


def main() -> None:
    svg = (PUBLIC / "favicon.svg").read_text()
    square = svg.replace('rx="7" fill="#161826"', 'fill="#161826"', 1)
    assert square != svg, "favicon.svg 의 바탕 사각형(rx 7·#161826)을 찾지 못했다"
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        rounded = shoot(svg, tmp)
        full = shoot(square, tmp)

    small(rounded, 512).save(PUBLIC / "logo-512.png", optimize=True)
    small(rounded, 192).save(PUBLIC / "icon-192.png", optimize=True)
    small(full, 180).convert("RGB").save(PUBLIC / "apple-touch-icon.png", optimize=True)
    frames = [small(rounded, n) for n in (16, 32, 48)]
    buf = io.BytesIO()
    frames[-1].save(buf, format="ICO", sizes=[(16, 16), (32, 32), (48, 48)], append_images=frames[:-1])
    (PUBLIC / "favicon.ico").write_bytes(buf.getvalue())
    for name in ("logo-512.png", "icon-192.png", "apple-touch-icon.png", "favicon.ico"):
        print(f"{name} {(PUBLIC / name).stat().st_size:,}B")


if __name__ == "__main__":
    main()
