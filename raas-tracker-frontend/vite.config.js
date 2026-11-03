import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    rolldownOptions: {
      output: {
        // Stable vendor chunks shared by every lazy route. Recharts is large
        // and only Home/Reports use it, so it stays out of the entry payload.
        codeSplitting: {
          groups: [
            {
              // clsx is shared by the entry (Button) and by Recharts; without
              // its own group the charts chunk would absorb it and become a
              // static entry dependency, defeating the lazy charts.
              name: 'shared',
              test: /node_modules[\\/]clsx[\\/]/,
              priority: 40,
            },
            {
              name: 'react',
              test: /node_modules[\\/](react|react-dom|react-router|react-router-dom|scheduler)[\\/]/,
              priority: 30,
            },
            {
              name: 'charts',
              test: /node_modules[\\/](recharts|victory-vendor|d3-[^\\/]+|react-smooth|internmap|decimal\.js-light|@reduxjs[\\/]toolkit|react-redux|redux|immer|reselect|es-toolkit|eventemitter3|use-sync-external-store|tiny-invariant)[\\/]/,
              priority: 20,
            },
          ],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:5000'
    }
  }
})
