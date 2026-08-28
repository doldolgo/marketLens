import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// dev 프록시: /api/* → localhost:8000, /api 접두사는 떼고 전달 (스펙 002 §3.1)
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
