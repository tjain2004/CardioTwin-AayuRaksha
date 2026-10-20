import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Patient phone app: dev server on 5174, preview of the production build on 4174. The doctor dashboard uses 5173 / 4173.
// host: true listens on the Wi-Fi address too, so a phone on the same network can open it.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5174, strictPort: true, host: true },
  preview: { port: 4174, strictPort: true, host: true },
})
