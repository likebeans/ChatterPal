import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  return {
    plugins: [react()],
    server: {
      port: 3010,
      proxy: {
        '/api': {
          target: env.CHATTERPAL_API_TARGET || 'http://localhost:8010',
          changeOrigin: true,
        },
      },
    },
  }
})