// 수집 상태 탭 표시 전용 상수 — 응답 타입(HealthData)은 셸도 읽으므로 shared/types 에 있다 (스펙 011 §3.6).
import type { HealthState, OutageKind } from '../../shared/types'

/** 유형 칩 라벨 (§3.8). Record 가 종류 union 전체를 덮으므로 종류가 늘면 타입 검사가 막는다. */
export const KIND_LABELS: Record<OutageKind, string> = {
  timeout: '타임아웃',
  network: '연결 실패',
  rate_limit: 'rate limit',
  banned: '차단',
  unavailable: '거래소 오류',
  bad_request: '요청 오류',
  bad_response: '응답 오류',
  stale_stream: '스트림 정체',
}

/** 거래소 카드 상태 문구 (§3.8). */
export const STATE_LINES: Record<HealthState, { mark: string; text: string }> = {
  ok: { mark: '●', text: '수집 중' },
  stale: { mark: '◌', text: '지연' },
  down: { mark: '✕', text: '끊김' },
}
