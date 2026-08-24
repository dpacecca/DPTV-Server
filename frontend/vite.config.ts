import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    proxy: {
      '/api': 'http://localhost:8000',
      '/player_api.php': 'http://localhost:8000',
      '/get.php': 'http://localhost:8000',
      '/xmltv.php': 'http://localhost:8000',
      '/live': 'http://localhost:8000',
      '/movie': 'http://localhost:8000',
      '/series': 'http://localhost:8000',
    },
  },
})
