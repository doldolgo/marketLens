"""line protocol 한 줄 → InfluxPoint — Influx fake 들이 `write_lines` 로 받은 줄을 점으로 되돌릴 때 쓴다.

`core.influx.to_line` 의 역이다: 태그 이스케이프(`\\,` `\\ ` `\\=` `\\\\`), 문자열 필드(따옴표 안 `\\"` `\\\\`),
정수 필드(`i` 접미), 나머지는 float. 테스트 도구라 앱이 쓰는 모양만 다룬다.
"""

from app.core.influx import InfluxPoint


def _split_unescaped(text: str, sep: str) -> list[str]:
    """역슬래시로 이스케이프되지 않은 `sep` 에서 자른다(이스케이프는 그대로 둔다)."""
    out: list[str] = []
    cur: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            cur.append(text[i : i + 2])
            i += 2
            continue
        if ch == sep:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    out.append("".join(cur))
    return out


def _unescape(text: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            out.append(text[i + 1])
            i += 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _fields(text: str) -> dict[str, float | int | str]:
    fields: dict[str, float | int | str] = {}
    i = 0
    while i < len(text):
        eq = text.index("=", i)
        key = text[i:eq]
        i = eq + 1
        if text[i] == '"':
            j = i + 1
            chars: list[str] = []
            while text[j] != '"':
                if text[j] == "\\":
                    chars.append(text[j + 1])
                    j += 2
                else:
                    chars.append(text[j])
                    j += 1
            fields[key] = "".join(chars)
            i = j + 1
        else:
            end = text.find(",", i)
            end = len(text) if end < 0 else end
            token = text[i:end]
            fields[key] = int(token[:-1]) if token.endswith("i") else float(token)
            i = end
        if i < len(text) and text[i] == ",":
            i += 1
    return fields


def parse_line(line: str) -> InfluxPoint:
    rest, ts = line.rsplit(" ", 1)
    parts = _split_unescaped(rest, " ")
    key, field_text = parts[0], " ".join(parts[1:])
    measurement, *tag_parts = _split_unescaped(key, ",")
    tags: dict[str, str] = {}
    for part in tag_parts:
        k, v = _split_unescaped(part, "=")
        tags[_unescape(k)] = _unescape(v)
    return InfluxPoint(
        measurement=measurement, tags=tags, fields=_fields(field_text), ts=int(ts)
    )
