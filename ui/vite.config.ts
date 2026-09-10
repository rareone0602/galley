import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Two ways to run the UI.
//
//  - `npm run build`, and the backend serves the result itself on port 8124.
//    That is `./run.sh`, and it is what you want for ordinary use.
//  - `npm run dev` (or `./dev.sh`), and Vite serves it from source on 5173,
//    forwarding every /api call through to the backend. Editing a component
//    swaps it into the running page; no build step.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8124',
        changeOrigin: true,
        // The session log is one HTTP response that never ends. Without this
        // the proxy hangs up on it after two minutes and the log goes quiet
        // in dev but not in a built copy — which is a bad afternoon.
        timeout: 0,
        proxyTimeout: 0,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        // Three things dominate the bundle and none of them changes when
        // Galley does. Splitting them out means an ordinary edit rebuilds and
        // re-downloads only Galley's own code, and the browser keeps the rest.
        // `@codemirror/legacy-modes` is deliberately absent: it publishes no
        // root entry, only `…/mode/<name>`, so naming it here fails the build.
        manualChunks: {
          pdf: ['pdfjs-dist'],
          editor: [
            'codemirror',
            '@codemirror/state',
            '@codemirror/view',
            '@codemirror/commands',
            '@codemirror/language',
            '@codemirror/search',
            '@codemirror/autocomplete',
            '@lezer/highlight',
          ],
          react: ['react', 'react-dom', 'react-resizable-panels'],
        },
      },
    },
  },
})
