import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
const apiTarget = process.env.RAG_API_TARGET || 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': apiTarget,
    },
  },
  preview: {
    port: 4173,
    proxy: {
      '/api': apiTarget,
    },
  },
})

