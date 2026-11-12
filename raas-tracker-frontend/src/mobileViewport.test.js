import { describe, it, expect } from 'vitest';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join, relative } from 'node:path';

// Vitest runs with the frontend package as cwd (npm test / CI both do).
const ROOT = process.cwd();
const SRC = join(ROOT, 'src');
const PUBLIC = join(ROOT, 'public');

function sourceFiles(dir = SRC) {
  const found = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      found.push(...sourceFiles(full));
    } else if (
      (entry.name.endsWith('.jsx') || entry.name.endsWith('.js'))
      && !entry.name.includes('.test.')
    ) {
      found.push(full);
    }
  }
  return found;
}

// A legacy fixed screen-height utility (Tailwind's `h-` + `screen` pair, used
// bare or with the `min-` prefix) pins the layout to the *large* viewport, so
// the mobile address bar cuts the bottom of the app; the dynamic-viewport
// utilities follow the visible area instead.
// The pattern is assembled from parts on purpose: Tailwind's content scanner
// reads this file, and a literal class name here would emit that dead utility
// into the production stylesheet.
const FIXED_SCREEN_HEIGHT = new RegExp('\\bh-' + 'screen\\b');

function fixedScreenHeightSites() {
  const sites = [];
  for (const file of sourceFiles()) {
    const source = readFileSync(file, 'utf8');
    source.split('\n').forEach((line, index) => {
      if (FIXED_SCREEN_HEIGHT.test(line)) {
        sites.push(`${relative(SRC, file)}:${index + 1}`);
      }
    });
  }
  return sites;
}

function pngSize(file) {
  const buffer = readFileSync(file);
  const signature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  if (buffer.subarray(0, 8).equals(signature) === false) return null;
  return [buffer.readUInt32BE(16), buffer.readUInt32BE(20)];
}

function publicFile(urlPath) {
  return join(PUBLIC, urlPath.replace(/^\//, ''));
}

describe('dynamic viewport height', () => {
  it('uses no fixed screen-height class in the app shell', () => {
    expect(fixedScreenHeightSites()).toEqual([]);
  });
});

describe('document head', () => {
  const html = readFileSync(join(ROOT, 'index.html'), 'utf8');

  it('declares a mobile viewport that covers the safe area', () => {
    expect(html).toMatch(/<meta name="viewport"[^>]*width=device-width/);
    expect(html).toMatch(/<meta name="viewport"[^>]*initial-scale=1/);
    expect(html).toContain('viewport-fit=cover');
  });

  it('declares a meta description for search engines and link previews', () => {
    expect(html).toMatch(/<meta name="description" content="[^"]{20,}"/);
  });

  it('declares a theme colour, a manifest and an apple touch icon', () => {
    expect(html).toMatch(/<meta name="theme-color"[^>]*content="#[0-9a-f]{6}"/i);
    expect(html).toMatch(/<link rel="manifest" href="\/manifest\.json"/);

    const apple = html.match(/<link rel="apple-touch-icon" href="([^"]+)"/);
    expect(apple).toBeTruthy();
    expect(existsSync(publicFile(apple[1]))).toBe(true);
  });
});

describe('web app manifest', () => {
  const manifestPath = join(PUBLIC, 'manifest.json');

  it('declares an installable standalone app', () => {
    expect(existsSync(manifestPath)).toBe(true);
    const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));

    expect(manifest.name).toBeTruthy();
    expect(manifest.short_name).toBeTruthy();
    expect(manifest.start_url).toBe('/');
    expect(manifest.display).toBe('standalone');
    expect(manifest.theme_color).toMatch(/^#[0-9a-f]{6}$/i);
    expect(manifest.background_color).toMatch(/^#[0-9a-f]{6}$/i);
  });

  it('ships png icons whose files match their declared sizes', () => {
    const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
    const pngIcons = manifest.icons.filter((icon) => icon.type === 'image/png');
    const sizes = pngIcons.map((icon) => icon.sizes);

    expect(sizes).toContain('192x192');
    expect(sizes).toContain('512x512');

    for (const icon of pngIcons) {
      const file = publicFile(icon.src);
      expect(existsSync(file), icon.src).toBe(true);
      expect(pngSize(file)).toEqual(icon.sizes.split('x').map(Number));
    }
  });

  it('ships a 180px apple touch icon', () => {
    const file = join(PUBLIC, 'icons', 'apple-touch-icon.png');
    expect(existsSync(file)).toBe(true);
    expect(pngSize(file)).toEqual([180, 180]);
  });
});
