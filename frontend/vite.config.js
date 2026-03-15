import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 3000,
    proxy: {
      '/analyze': 'http://localhost:8000',
      '/feedback': 'http://localhost:8000',
      '/metrics': 'http://localhost:8000',
      '/recent': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
})
