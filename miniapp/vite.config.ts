/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const backend = 'http://127.0.0.1:8000';

export default defineConfig({
  // The built app is served by Django under /app/.
  base: '/app/',
  plugins: [react()],
  server: {
    proxy: {
      '/api': backend,
      '/media': backend,
    },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
});
