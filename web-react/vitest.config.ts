import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: {
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    testTimeout: 10000,
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts',
  },
});
