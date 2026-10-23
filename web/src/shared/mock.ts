// 해외 거래소 목록 — 스프레드 표의 "비교 해외 거래소" 체크박스 목록이자 URL 필터의 허용 이름 (스펙 002 §3.6).
// mock 시세(코인 24개·현물/선물 항목)는 마지막 사용자였던 선선갭 탭이 실데이터(049)가 되면서 지웠다.

/** 해외 거래소 7곳 — 이 목록 밖 이름은 URL 필터에서 버린다. OKX 는 045. */
export const FX_EXS = ['Binance', 'Bybit', 'Bitget', 'OKX', 'MEXC', 'Gate.io', 'Hyperliquid'] as const
