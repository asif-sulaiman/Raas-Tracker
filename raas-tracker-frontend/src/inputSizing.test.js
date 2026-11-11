import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';
import { join, relative } from 'node:path';

// Vitest runs with the frontend package as cwd (npm test / CI both do).
const SRC = join(process.cwd(), 'src');

function jsxSources(dir = SRC) {
  const found = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      found.push(...jsxSources(full));
    } else if (entry.name.endsWith('.jsx') && !entry.name.includes('.test.')) {
      found.push(full);
    }
  }
  return found;
}

// Read one JSX opening tag from `<input` to its closing `>`, honouring quotes
// and brace depth so `onChange={(e) => ...}` does not end the tag at the arrow.
function readTag(source, start) {
  let quote = null;
  let depth = 0;
  for (let i = start; i < source.length; i += 1) {
    const char = source[i];
    if (quote) {
      if (char === quote && source[i - 1] !== '\\') quote = null;
      continue;
    }
    if (char === '"' || char === "'" || char === '`') {
      quote = char;
    } else if (char === '{') {
      depth += 1;
    } else if (char === '}') {
      depth -= 1;
    } else if (char === '>' && depth === 0) {
      return source.slice(start, i + 1);
    }
  }
  return source.slice(start);
}

function numericInputs() {
  const inputs = [];
  for (const file of jsxSources()) {
    const source = readFileSync(file, 'utf8');
    const opener = /<input\b/g;
    let match;
    while ((match = opener.exec(source))) {
      const tag = readTag(source, match.index);
      if (tag.includes('type="number"')) {
        inputs.push({
          where: `${relative(SRC, file)}:${source.slice(0, match.index).split('\n').length}`,
          tag,
        });
      }
    }
  }
  return inputs;
}

// Input-length controls included in this slice. `readOnlyCls` composes inputCls
// and inherits the fix, so it is not listed separately.
const INPUT_CLASS_CONSTANT = /const\s+(inputCls|fieldCls|rowInputCls)\s*=\s*([\s\S]*?);/g;
const BARE_SMALL_TEXT = /\btext-(xs|sm)\b/;
const RESPONSIVE_SMALL_TEXT = /\bsm:text-(xs|sm)\b/g;

function classConstants() {
  const constants = [];
  for (const file of jsxSources()) {
    const source = readFileSync(file, 'utf8');
    let match;
    while ((match = INPUT_CLASS_CONSTANT.exec(source))) {
      constants.push({
        where: `${relative(SRC, file)}:${source.slice(0, match.index).split('\n').length}`,
        name: match[1],
        value: match[2],
      });
    }
    INPUT_CLASS_CONSTANT.lastIndex = 0;
  }
  return constants;
}

describe('numeric input keyboard hints', () => {
  it('declares inputMode on every type="number" input', () => {
    const inputs = numericInputs();
    expect(inputs.length).toBeGreaterThan(0);
    const missing = inputs.filter(({ tag }) => !tag.includes('inputMode='));
    expect(missing.map(({ where }) => where)).toEqual([]);
  });
});

describe('input text size on phones', () => {
  it('keeps every input class constant at 16px below sm', () => {
    const constants = classConstants();
    expect(constants.length).toBeGreaterThan(0);
    const offenders = constants
      .filter(({ value }) => !value.includes('text-base'))
      .map(({ where, name }) => `${where} ${name}`);
    expect(offenders).toEqual([]);
  });

  it('keeps inline numeric input classes at 16px below sm', () => {
    const offenders = numericInputs()
      .filter(({ tag }) => {
        const classAttr = tag.match(/className="([^"]*)"/);
        if (!classAttr) return false;
        const withoutResponsive = classAttr[1].replace(RESPONSIVE_SMALL_TEXT, '');
        return BARE_SMALL_TEXT.test(withoutResponsive);
      })
      .map(({ where }) => where);
    expect(offenders).toEqual([]);
  });
});
