import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Watch UI in the browser: dev server on 5175, preview of the production build on 4175.
// The doctor dashboard uses 5173 / 4173 and the patient phone app 5174 / 4174.
export default defineConfig({
  plugins: [react()],
  server: { port: 5175, strictPort: true, host: true },
  preview: { port: 4175, strictPort: true, host: true },
})
