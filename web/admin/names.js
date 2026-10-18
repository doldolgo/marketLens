// 이름표(스펙 066 — 064 의 표를 옮김) — 피드의 키 → 화면 이름. 표에 없는 값은 원래 글자 그대로(clean). 색 열쇠도 여기서
// 정한다: 색은 순위가 아니라 대상을 따른다(걸러도 남은 것의 색이 바뀌지 않게) — 표 순서대로 차트 8색, 그 밖은 '기타' 회색.
import { clean, own } from './common.js';

const nameIn = (table) => (key) => own(table, key) ?? clean(key);
// 표 안 순서 → 색 열쇠 k1~k8(조사 §5 차트 8색), 표 밖·9번째부터 kx
const markIn = (table) => (key) => {
  const at = Object.keys(table).indexOf(key);
  return at >= 0 && at < 8 ? `k${at + 1}` : 'kx';
};

// 수집 원천 — /health/collect 순서(현물 6 → perp 4, 011·046·047)
export const EX_NAME = Object.freeze({
  upbit: '업비트',
  bithumb: '빗썸',
  binance: '바이낸스',
  bybit: '바이빗',
  bitget: '비트겟',
  okx: 'OKX',
  binance_perp: '바이낸스 perp',
  bybit_perp: '바이빗 perp',
  bitget_perp: '비트겟 perp',
  hyperliquid_perp: 'Hyperliquid',
});
export const exName = nameIn(EX_NAME);

// 수집 실패 종류(011 — 공개 수집 상태 탭의 유형 칩과 같다)
export const KIND_NAME = Object.freeze({
  timeout: '타임아웃',
  network: '연결 실패',
  rate_limit: 'rate limit',
  banned: '차단',
  unavailable: '거래소 오류',
  bad_request: '요청 오류',
  bad_response: '응답 오류',
  stale_stream: '스트림 정체',
});
export const kindName = nameIn(KIND_NAME);

// 상자 셋 — 색 고정(collect --c1 · data --c2 · serve --c3, 066 §3.1)
export const BOXES = Object.freeze(['collect', 'data', 'serve']);
export const BOX_ROLE = Object.freeze({ collect: '수집기', data: 'Influx·Redis', serve: 'caddy·web·api' });
export const boxMark = markIn({ collect: 1, data: 2, serve: 3 });

// 접속 요약(038·039·052·062)
export const CHANNEL_NAME = Object.freeze({
  direct: '직접',
  search: '검색',
  inapp: '앱 안 브라우저',
  social: '소셜·커뮤니티',
  ai: 'AI 답변',
  referral: '다른 사이트 링크',
  campaign: '캠페인(utm)',
  internal: '사이트 안 이동',
  unknown: '첫 페이지 기록 없음',
});
export const PAGE_NAME = Object.freeze({
  landing: '랜딩',
  'app-spread': '대시보드 스프레드',
  'app-history': '대시보드 기록',
  'app-gap': '대시보드 갭',
  'app-pp': '대시보드 선선갭',
  'app-health': '대시보드 수집 상태',
  'app-flow': '대시보드 입출금 레이더',
  privacy: '처리방침',
  'kimp-chart': '김프 차트',
  'kimp-history': '김프 기록',
});
export const NET_NAME = Object.freeze({ telecom_kr: '국내 통신사', telecom: '해외 통신사', cloud: '데이터센터', other: '그 밖', unknown: '모름', ipv6: 'IPv6' });
// 요청 종류(038 §3.4 의 키 → 082 §3.1 이름) — 처음 보는 사람이 무슨 접근인지 알 이름, 아래는 '누가' 판의 흐린 설명
export const CLASS_NAME = Object.freeze({
  browser: '일반 브라우저',
  search: '검색 로봇',
  ai: 'AI 로봇',
  preview: '링크 미리보기',
  tool: '자동화 도구',
  scanner: '그 밖 봇·스캐너',
  operator: '운영자 접속',
  unknown: '브라우저 정보 없음',
});
export const CLASS_NOTE = Object.freeze({
  browser: '크롬·사파리 같은 보통 브라우저로 온 요청 — 사람이 대부분이지만 브라우저인 척하는 봇도 섞인다',
  search: '구글·네이버 같은 검색엔진이 페이지를 읽어 가는 로봇',
  ai: 'GPTBot·ClaudeBot 같은 AI 학습·검색 로봇',
  preview: '카카오톡·슬랙 등에 링크를 붙였을 때 미리보기를 만드는 요청',
  tool: 'curl·파이썬·헤드리스 브라우저·감시 도구 같은 프로그램',
  scanner: '취약점을 찾는 요청(/.env·/wp-login.php 등)과 위 목록에 없는 봇',
  operator: '운영자가 관리자 화면·Clarity·로컬 시험에서 띄운 요청 — 방문자로 세지 않는다',
  unknown: '자기가 무엇인지(브라우저 정보)를 비워 보낸 요청',
});
// 최근 요청 경로의 '페이지·파일' 배지(082 §3.2) — 주소를 직접 연 페이지 · 그 페이지가 열리며 함께 받은 파일 · 페이지 없이 따로 부른 파일
export const HOW_NAME = Object.freeze({ page: '직접 연 페이지', with: '함께 받은 파일', alone: '따로 부른 파일' });
export const DEVICE_NAME = Object.freeze({ desktop: '데스크톱', mobile: '휴대폰', tablet: '태블릿' });
export const OS_NAME = Object.freeze({ windows: 'Windows', macos: 'macOS', ios: 'iOS', android: 'Android', linux: 'Linux', chromeos: 'ChromeOS', other: '그 밖' });
export const BROWSER_NAME = Object.freeze({
  chrome: 'Chrome',
  safari: 'Safari',
  samsung: '삼성 인터넷',
  whale: '웨일',
  edge: 'Edge',
  firefox: 'Firefox',
  opera: 'Opera',
  inapp: '앱 안 브라우저',
  other: '그 밖',
});
export const TAB_NAME = Object.freeze({ spread: '실시간 스프레드', history: '기록/통계', gap: '선물–현물 갭', pp: '선선갭', health: '수집 상태', flow: '입출금 레이더' });
export const channelName = nameIn(CHANNEL_NAME);
export const pageName = nameIn(PAGE_NAME);
export const netName = nameIn(NET_NAME);
export const className = nameIn(CLASS_NAME);
export const deviceName = nameIn(DEVICE_NAME);
export const osName = nameIn(OS_NAME);
export const browserName = nameIn(BROWSER_NAME);
export const tabName = nameIn(TAB_NAME);
export const channelMark = markIn(CHANNEL_NAME);
export const netMark = markIn(NET_NAME);
export const classMark = markIn(CLASS_NAME);
export const deviceMark = markIn(DEVICE_NAME);
export const osMark = markIn(OS_NAME);
export const browserMark = markIn(BROWSER_NAME);

// 응답 상태 — 뜻이 상태라 상태색(2xx 정상·4xx 주의·5xx 문제), 3xx 는 차트 1번, WS 재접속 실패는 8번
export const STATUS_KEYS = Object.freeze([
  ['2xx', '2xx', 'kok'],
  ['3xx', '3xx', 'k1'],
  ['4xx', '4xx', 'kwarn'],
  ['5xx', '5xx', 'kbad'],
  ['ws5xx', 'WS 재접속 실패', 'k8'],
]);

// Clarity(040·068)
export const SIGNALS = Object.freeze([
  ['deadClick', '죽은 클릭'],
  ['rageClick', '분노 클릭'],
  ['excessiveScroll', '과한 스크롤'],
  ['quickback', '빠른 뒤로'],
  ['scriptError', '스크립트 오류'],
  ['errorClick', '오류 클릭'],
]);
export const CLARITY_PAGE = Object.freeze({
  landing: '랜딩',
  'app:spread': '대시보드 스프레드',
  'app:history': '대시보드 기록',
  'app:gap': '대시보드 갭',
  'app:pp': '대시보드 선선갭',
  'app:health': '대시보드 수집 상태',
  'app:flow': '대시보드 입출금 레이더',
  'app:other': '대시보드 그 밖',
  privacy: '처리방침',
  other: '그 밖 페이지',
});
export const CLARITY_DEVICE = Object.freeze({ mobile: '휴대폰', tablet: '태블릿', desktop: '데스크톱', other: '그 밖' });
export const clarityPage = nameIn(CLARITY_PAGE);
export const clarityDevice = nameIn(CLARITY_DEVICE);

// 기간·비교 — 단추 글자와 비교 한 줄('어제 같은 때보다 ▲ 12%')의 앞말
export const RANGE_NAME = Object.freeze({ '1h': '1시간', '6h': '6시간', '24h': '24시간', '7d': '7일', '30d': '30일', '90d': '90일' });
export const CMP_NAME = Object.freeze({ none: '없음', prev: '이전 기간', week: '지난주 같은 때' });
const PREV_PHRASE = Object.freeze({ '1h': '한 시간 전', '6h': '6시간 전', '24h': '어제 같은 때', '7d': '앞 7일', '30d': '앞 30일', '90d': '앞 90일' });
export const cmpPhrase = (range, cmp) => (cmp === 'week' ? '지난주 같은 때' : (own(PREV_PHRASE, range) ?? '이전 기간'));

// 나라 — 국기 그림 문자 + 브라우저의 한국어 지역 이름. 못 푸는 값(ZZ·(기타))은 글자 그대로
const REGION = typeof Intl.DisplayNames === 'function' ? new Intl.DisplayNames(['ko'], { type: 'region' }) : null;
const isCode = (code) => typeof code === 'string' && /^[A-Z]{2}$/.test(code) && code !== 'ZZ';
// 국기 그림 문자만(070 위치 칸 — '🇰🇷 서울 · KT'), 못 푸는 값은 빈 글자
export const flagOf = (code) => (isCode(code) ? String.fromCodePoint(...[...code].map((ch) => 0x1f1e6 + ch.charCodeAt(0) - 65)) : '');
export function countryName(code) {
  if (!isCode(code)) return clean(code);
  try {
    return REGION?.of(code) ?? code;
  } catch {
    return code;
  }
}
export const countryLabel = (code) => (isCode(code) ? `${flagOf(code)} ${countryName(code)}` : clean(code));
