import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.ts'],
    include: ['src/**/*.test.{js,jsx,ts,tsx}'],
    // Vitest's own per-test timeout defaults to 5000 ms and bounds the whole
    // test. GitHub's runners are slower than a dev machine and run the files
    // in parallel, so the Recipes focus test and the Reports pagination test
    // could exceed it under load even though both pass in isolation. The
    // assertions are unchanged; only the budget grows. This is a different
    // knob from Testing Library's `asyncUtilTimeout` in src/test-setup.ts,
    // which bounds a single async assertion.
    testTimeout: 15000,
    hookTimeout: 15000,
  },
});
