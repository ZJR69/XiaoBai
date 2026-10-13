import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 本地开发：/api 代理到 FastAPI 后端（API_PORT 可覆盖，默认 8000——冒烟测试用临时端口时设它）
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': `http://localhost:${process.env.API_PORT || 8000}`,
    },
  },
})
