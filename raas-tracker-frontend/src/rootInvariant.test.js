import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';
import { join, relative } from 'node:path';

// Vitest runs with the frontend package as cwd (npm test / CI both do).
const SRC = join(process.cwd(), 'src');

function sources(dir = SRC) {
  const found = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      found.push(...sources(full));
    } else if (
      (entry.name.endsWith('.jsx') || entry.name.endsWith('.js') || entry.name.endsWith('.tsx'))
      && !entry.name.includes('.test.')
    ) {
      found.push(full);
    }
  }
  return found;
}

// The dialog layer keeps its stack of open layers in module scope, so the app
// must mount exactly one React root: with two roots, a dialog opened in one
// would trap the focus of the other, and one root's Escape would close the
// other's layer. This pins the assumption instead of leaving it in a comment.
describe('React root', () => {
  it('mounts exactly one React root', () => {
    const roots = [];
    for (const file of sources()) {
      const source = readFileSync(file, 'utf8');
      const matches = source.match(/createRoot\(/g) || [];
      if (matches.length > 0) {
        roots.push(`${relative(SRC, file)}: ${matches.length}`);
      }
    }
    expect(roots).toEqual(['main.jsx: 1']);
  });
});
