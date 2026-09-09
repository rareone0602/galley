import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The backend serves the built assets itself, so `dev` only needs to reach the
// API. Both go through port 8124.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8124',
    },
  },
  build: { outDir: 'dist', emptyOutDir: true },
})
