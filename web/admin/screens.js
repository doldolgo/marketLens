// 화면 페이지(스펙 066 §3.3) — 화면 분석 창은 065 몫이다. 이 스펙에서는 내비 항목과 '준비 중' 한 줄만 두고, 틀(내비 모양·시계)만 돈다.
import { start } from './app.js';

start({ id: 'screens', home: '/screens.html', ranges: ['24h'], range: '24h', needs: () => [], cards: [] });
