"""접속 요약의 망 종류 판정 표 — ASN 표·조직 이름 낱말·맞추기 규칙 (스펙 039 §3.4).

DB-IP ASN 자료를 올릴 때 행마다 한 번 부른다. 위에서 먼저 맞은 것이 이긴다 — 표의 ASN → `cloud` 낱말 → `telecom` 낱말 →
그 밖 `other`. ASN 자료에 없는 구간(`unknown`)과 국내 통신사(`telecom_kr` — 나라가 KR 이고 `telecom`)는 조회 때 정한다.
조직 이름과 AS 번호는 종류를 정하는 데만 쓰고 버린다 — 응답·로그에는 종류 이름만 나간다.
"""

import re

TELECOM_KR = "telecom_kr"
TELECOM = "telecom"
CLOUD = "cloud"
OTHER = "other"
UNKNOWN = "unknown"
# 응답 `networks` 의 이름 다섯 — 적재 표에 드는 것은 앞의 셋(telecom·cloud·other)
KINDS = (TELECOM_KR, TELECOM, CLOUD, OTHER, UNKNOWN)

# 1. 표의 ASN — 이름 낱말로 안 잡히는 큰 클라우드·국내 통신사
CLOUD_ASNS = frozenset(
    (
        16509,  # Amazon
        14618,
        15169,  # Google
        396982,
        8075,  # Microsoft
        13335,  # Cloudflare
        14061,  # DigitalOcean
        16276,  # OVH
        24940,  # Hetzner
        63949,  # Akamai/Linode
        20473,  # Vultr
        31898,  # Oracle
        45102,  # Alibaba
        132203,  # Tencent
        51167,  # Contabo
        12876,  # Scaleway
        60781,  # Leaseweb
        9009,  # M247
    )
)
TELECOM_ASNS = frozenset(
    (
        4766,  # KT
        9318,  # SK브로드밴드
        9644,  # SK텔레콤
        3786,  # LG유플러스
        17858,
    )
)
# 2·3. 조직 이름 낱말 — 낱말이 표 낱말로 시작하면 맞다(네 글자 이하는 낱말 전체가 같아야). `data center` 는 아래에서 따로
CLOUD_WORDS = (
    "hosting cloud datacenter server vps colo colocation dedicated idc".split()
)
TELECOM_WORDS = (
    "telecom telekom broadband mobile wireless cable communication isp".split()
)
SHORT = 4  # 이 길이 이하 표 낱말은 전체 일치 — Colombia·Dispatch·VPSX 를 잡지 않게
# 낱말 = 영문자·숫자가 이어진 것. 낱말로 나눠 비교하는 규칙을 정규식 하나로 — 낱말 시작(앞이 영숫자 아님)에서 표 낱말로
# 시작하면 맞고, 짧은 낱말은 끝(뒤가 영숫자 아님)까지 같아야 한다. 7만여 ASN 을 나눠 비교하면 ≈0.21초, 정규식은 ≈0.09초
_START, _END = "(?<![a-z0-9])", "(?![a-z0-9])"


def _pattern(words: list[str], extra: str = "") -> re.Pattern[str]:
    long = "|".join(re.escape(w) for w in words if len(w) > SHORT)
    short = "|".join(re.escape(w) for w in words if len(w) <= SHORT)
    parts = [f"{_START}(?:{long})", f"{_START}(?:{short}){_END}"]
    return re.compile("|".join(parts + ([extra] if extra else [])))


# `data center` 는 이어진 두 낱말 data·center(`center` 는 앞 맞춤 — Data Centers 도)
_CLOUD = _pattern(CLOUD_WORDS, f"{_START}data[^a-z0-9]+center")
_TELECOM = _pattern(TELECOM_WORDS)


def network_kind(asn: int, org: str) -> str:
    """ASN 행 하나의 망 종류 — `cloud`·`telecom`·`other` 중 하나. 조직 이름은 소문자로 바꿔 본다."""
    if asn in CLOUD_ASNS:
        return CLOUD
    if asn in TELECOM_ASNS:
        return TELECOM
    low = org.lower()
    if _CLOUD.search(low) is not None:
        return CLOUD
    if _TELECOM.search(low) is not None:
        return TELECOM
    return OTHER
