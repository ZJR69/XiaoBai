import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 本地开发：/api 代理到 FastAPI 后端
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
