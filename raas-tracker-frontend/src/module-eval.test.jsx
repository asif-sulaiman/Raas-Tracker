// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';

// Every source module must evaluate cleanly at import time.
//
// A stray token left at top level (e.g. a heredoc `EOF` terminator written
// into a component) is a bare identifier: it throws ReferenceError the moment
// the module is imported. That kills the whole bundle before React mounts —
// production renders a blank page — while every other test stays green,
// because nothing imported that module. Importing everything closes that hole.
const modules = import.meta.glob('./**/*.{js,jsx}');

const skip = (p) =>
  p.includes('.test.') ||
  p.endsWith('/test-setup.ts') ||
  p.endsWith('/main.jsx') ||
  p.endsWith('/vite-env.d.ts');

describe('module evaluation', () => {
  // Importing ~100 modules exceeds vitest's 5s default under full-suite load.
  it('every source module imports without throwing', { timeout: 120_000 }, async () => {
    const failures = [];
    for (const [path, load] of Object.entries(modules)) {
      if (skip(path)) continue;
      try {
        await load();
      } catch (err) {
        failures.push(`${path}: ${err?.message || err}`);
      }
    }
    expect(failures.join('\n')).toBe('');
  });
});
