import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.ts'],
    include: ['src/**/*.test.{js,jsx,ts,tsx}'],
    // GitHub's runners are slower than a dev machine and run the files in
    // parallel, so the 5 s default made the Recipes focus test and the
    // Reports pagination test fail under load even though both pass in
    // isolation. The assertions are unchanged; only the budget grows.
    testTimeout: 15000,
    hookTimeout: 15000,
  },
});