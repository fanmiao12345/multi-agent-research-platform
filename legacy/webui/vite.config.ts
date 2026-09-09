import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发模式：npm run dev 起在 5173 端口，/api 请求代理到后端的 8765 端口
// （生产模式：python web_server.py 直接托管本目录 build 出来的 dist/）
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8765',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 1000,
  },
})
