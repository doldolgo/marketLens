import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// dev 프록시: /api/* → localhost:8000, /api 접두사는 떼고 전달 (스펙 002 §3.1). ws: true 는 /api/ws/spreads 업그레이드용 (017 §3.5)
export default defineConfig({
  // 대시보드는 /app/ 아래에 산다 — / 는 정적 랜딩(public/landing.html, 스펙 022). 번들·favicon 경로가 /app/ 접두를 갖는다.
  base: '/app/',
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        ws: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
