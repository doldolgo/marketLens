// 화면 페이지의 이름표(스펙 065 §3.4) — 영역 id·요소 열쇠 → 한국어 이름. 글자는 부르는 쪽이 textContent 로만 넣는다(066 XSS 규칙).
// AREA_NAMES 는 053 의 표 그대로(화면별 표 + 대시보드 공통 "app"), EL_NAMES 는 061 §7 '요소 목록' 의 열쇠(화면마다 뜻이 다르면
// "<화면>|<열쇠>"). 둘 다 JSON 꼴(큰따옴표) — server/tests 가 052·061 의 목록을 모두 덮는지 본다.
// 템플릿 열쇠(row:·period:·fx: …)는 FAMILY 규칙, 목록에 없는 자동 열쇠(link:·btn: …)는 AUTO 규칙, 그 밖은 열쇠 그대로.
import { clean, own } from './common.js';
import { tabName } from './names.js';

export const OTHER_KEY = '(기타)'; // 061 — 그날 (화면, 기기)의 열쇠 300 을 넘어 묶인 것

const AREA_NAMES = {
  "landing": { "top": "머리 막대", "hero": "첫 화면", "analyze": "히스토리 분석", "events": "지난 7일 사건", "kimp": "김프와 역프란", "method": "계산 방법", "faq": "자주 묻는 질문", "foot": "바닥" },
  "privacy": { "top": "머리 막대", "changes": "변경 안내", "glance": "한눈에 보기", "consent": "화면 분석 동의 관리", "s1": "1. 처리 목적", "s2": "2. 항목과 보유 기간", "s3": "3. 파기", "s4": "4. 제3자 제공", "s5": "5. 처리 위탁", "s6": "6. 국외 이전", "s7": "7. 자동 수집 장치", "s8": "8. 정보주체의 권리", "s9": "9. 안전성 확보 조치", "s10": "10. 보호책임자", "s11": "11. 권익침해 구제", "s12": "12. 방침의 변경", "foot": "바닥" },
  "kimp-chart": { "top": "머리 막대", "what": "볼 수 있는 것", "read": "읽는 법", "coins": "코인별 바로 가기", "faq": "자주 묻는 질문", "foot": "바닥" },
  "kimp-history": { "top": "머리 막대", "live": "지난 7일 사건", "what": "남는 것", "how": "보는 법", "faq": "자주 묻는 질문", "foot": "바닥" },
  "app": { "header": "로고·수집 상태", "tabs": "탭 단추", "kpi": "KPI 줄" },
  "app-spread": { "filters": "필터 바", "table": "김프 표" },
  "app-history": { "filters": "사건 필터", "table": "티커별 사건 표", "summary": "선택 심볼 요약", "list": "사건 로그", "filters-2": "차트 도구 줄", "chart": "봉 차트" },
  "app-gap": { "filters": "필터 바", "table": "갭 표" },
  "app-pp": { "filters": "필터 바", "table": "선선갭 표" },
  "app-health": { "summary": "요약", "cards": "거래소 카드", "chart": "실패 구간 타임라인", "list": "최근 실패 구간" },
  "app-flow": { "filters": "필터 바", "table": "코인별 순유입", "table-2": "최근 전송" }
};

const EL_NAMES = {
  "privacy": "개인정보 처리방침 링크",
  "consent-settings": "화면 분석 설정 링크",
  "menu": "메뉴 단추(좁은 화면)",
  "filter-toggle": "필터 펼치기(폰)",
  "sort:col": "정렬 열 고르기(폰)",
  "sort:dir": "정렬 방향(폰)",
  "search": "심볼 검색칸",
  "app-flow|search": "코인 심볼 입력칸",
  "threshold": "하이라이트 임계값",
  "threshold-price": "가격갭 임계값",
  "threshold-funding": "펀딩갭 임계값",
  "only-over": "임계 초과만",
  "only-io": "입출금 열림만",
  "only-net": "네트워크 같음만",
  "view:kimp": "김프 기준",
  "view:rev": "역프 기준",
  "mode:entry": "진입 기준",
  "mode:exit": "정리 기준",
  "app-history|sort:sym": "티커 정렬",
  "app-gap|sort:price": "현물가 USDT 정렬",
  "app-pp|sort:gap": "가격갭 정렬",
  "tx": "거래 해시 링크",
  "nav:kimp": "김프·역프란 링크",
  "nav:method": "계산 방법 링크",
  "nav:faq": "자주 묻는 질문 링크",
  "app:history": "김프 기록과 차트 링크",
  "live:1": "지금 가장 큰 경로 카드",
  "faq:why-differ": "김프 사이트마다 숫자가 다른 이유",
  "faq:why-1000": "왜 $1,000 기준",
  "faq:rate": "환율은 무엇을 쓰나",
  "faq:blocked": "옮길 수 없는 경로 판단",
  "faq:cost": "가입이나 비용",
  "faq:reverse-term": "역김프와 역프",
  "faq:chart": "김프 차트와 과거 기록",
  "faq:realtime": "김프 차트는 실시간인가",
  "faq:vs-price": "코인 가격 차트와 다른 점",
  "faq:apps": "거래소 앱·트레이딩뷰",
  "faq:fees": "수수료와 전송 시간",
  "faq:range": "얼마나 과거까지",
  "faq:max-vs-live": "최고 %가 실시간 표보다 큰 이유",
  "faq:profit": "사건이 많으면 돈을 버는지"
};

// 템플릿 열쇠의 값 — 061 §7 의 이름(없는 값은 글자 그대로)
const FX = { all: '모두', binance: 'Binance', bybit: 'Bybit', bitget: 'Bitget', okx: 'OKX', mexc: 'MEXC', gateio: 'Gate.io', hyperliquid: 'Hyperliquid' };
const DOM = { all: '전체', upbit: '업비트', bithumb: '빗썸' };
const PERIOD = { '1h': '1시간', '4h': '4시간', '8h': '8시간', '12h': '12시간', '24h': '24시간', '3d': '3일', '7d': '1주', '30d': '1달', '90d': '3달' };
const INTERVAL = { '1m': '1분', '3m': '3분', '5m': '5분', '15m': '15분', '30m': '30분', '1h': '1시간', '4h': '4시간', '1d': '1일' };
const WINDOW = { '1h': '1시간', '6h': '6시간', '24h': '24시간' };
const DIR = { all: '전체', in: '입금', out: '출금', kimp: '김프', reverse: '역프' };
const COIN = { btc: '비트코인', eth: '이더리움', xrp: '리플', sol: '솔라나', doge: '도지코인' };
const SORT = {
  sym: '심볼', chg: '변동율', price: '국내가격', usd: '해외가격', fxex: '해외거래소', domex: '국내거래소', val: '김프(역프)', io: '입출금',
  net: '네트워크', ongoingsince: '상태', cnt: '횟수', maxdur: '최대 지속', avgdur: '평균 지속', maxpct: '최대 스프레드', avgpct: '평균 스프레드',
  last: '최신', gap: '갭', funding: '펀딩비', fund: '펀딩갭',
};
const pick = (table, v) => own(table, v) ?? v;

// 템플릿 열쇠(065 §3.4 — '<접두>' 또는 '<화면>|<접두>' → 값으로 이름). 값은 clean() 을 지난 글자
const FAMILY = {
  row: (v) => `코인 행 ${v}`,
  'app-history|row': (v) => `티커 행 ${v}`,
  'landing|row': (v) => `사건 행 ${v}`,
  'kimp-history|row': (v) => `사건 행 ${v}`,
  period: (v) => `기간 ${pick(PERIOD, v)}`,
  fx: (v) => `해외 ${v === 'all' ? '전체' : pick(FX, v)}`,
  'app-spread|fx': (v) => `비교 해외 거래소 ${pick(FX, v)}`,
  dom: (v) => `국내 ${pick(DOM, v)}`,
  'app-spread|dom': (v) => `기준 국내 거래소 ${v === 'all' ? '모두' : pick(DOM, v)}`,
  'chart-dom': (v) => `차트 국내 ${pick(DOM, v)}`,
  sort: (v) => `${pick(SORT, v)} 정렬`,
  interval: (v) => `봉 종류 ${pick(INTERVAL, v)}`,
  card: (v) => `차트 카드 ${pick(FX, v)}`,
  fold: (v) => `카드 접기 ${pick(FX, v)}`,
  coin: (v) => (v === 'all' ? '전체 코인 김프 차트' : `${pick(COIN, v)} 김프 차트 바로 가기`),
  live: (v) => `경로 카드 ${v}위`,
  window: (v) => `창 ${pick(WINDOW, v)}`,
  dir: (v) => `방향 ${pick(DIR, v)}`,
};
// 목록에 없는 자동 열쇠(061 §3.1 — <종류>:<이름>)
const AUTO = {
  link: (v) => (v === 'mailto' ? '메일 링크' : `링크 ${v}`),
  btn: (v) => `단추 ${v}`,
  faq: (v) => `질문 ${v}`,
  input: (v) => `입력칸 ${v}`,
  select: (v) => `고르기 ${v}`,
  check: (v) => `체크 상자 ${v}`,
  tab: (v) => `탭 ${tabName(v)}`,
};

// 영역 이름 — 그 화면의 표, 대시보드는 공통 "app" 다음, 없으면 id 그대로
export function areaName(page, id) {
  const mine = own(AREA_NAMES, page) ?? {};
  const common = String(page).startsWith('app-') ? AREA_NAMES.app : {};
  return own(mine, id) ?? own(common, id) ?? clean(id);
}

// 요소 이름 — "<화면>|<열쇠>" → 열쇠 → 템플릿 규칙 → 자동 열쇠 규칙 → 열쇠 그대로
export function elName(page, key) {
  const k = String(key ?? '');
  if (k === OTHER_KEY) return '(기타 — 열쇠 300 넘음)';
  const named = own(EL_NAMES, `${page}|${k}`) ?? own(EL_NAMES, k);
  if (named !== undefined) return named;
  const at = k.indexOf(':');
  if (at > 0 && at < k.length - 1) {
    const kind = k.slice(0, at);
    const rule = own(FAMILY, `${page}|${kind}`) ?? own(FAMILY, kind) ?? own(AUTO, kind);
    if (rule) return rule(clean(k.slice(at + 1)));
  }
  return clean(k);
}
