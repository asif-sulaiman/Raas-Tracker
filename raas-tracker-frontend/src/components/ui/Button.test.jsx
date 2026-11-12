// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Pencil } from 'lucide-react';
import Button, { ICON_HIT_AREA } from './Button.jsx';

afterEach(cleanup);

// The shared contract: a centred 44x44 pseudo-element, so the control keeps
// its visual box while the pointer target meets the 44px touch guideline.
const HIT_AREA_TOKENS = [
  'relative',
  'after:absolute',
  'after:left-1/2',
  'after:top-1/2',
  'after:size-11',
  'after:-translate-x-1/2',
  'after:-translate-y-1/2',
  "after:content-['']",
];

describe('icon-only buttons', () => {
  it('adds the shared 44px hit area without changing the visual padding', () => {
    render(<Button size="sm" icon={Pencil} aria-label="Edit" />);
    const button = screen.getByRole('button', { name: 'Edit' });

    for (const token of HIT_AREA_TOKENS) {
      expect(button.className).toContain(token);
    }
    expect(button.className).toContain(ICON_HIT_AREA);
    expect(button.className).toContain('px-2.5');
    expect(button.className).toContain('py-1.5');
  });

  it('covers a bare loading button too', () => {
    render(<Button loading aria-label="Saving" />);
    expect(screen.getByRole('button', { name: 'Saving' }).className)
      .toContain(ICON_HIT_AREA);
  });
});

describe('text buttons', () => {
  it('leaves dense table buttons at their visual size', () => {
    render(<Button icon={Pencil}>Edit</Button>);
    const button = screen.getByRole('button', { name: 'Edit' });
    expect(button.className).not.toContain('after:size-11');
  });

  it('treats a child that renders nothing as no child at all', () => {
    render(<Button icon={Pencil}>{false}</Button>);
    expect(screen.getByRole('button', { name: '' }).className).toContain(ICON_HIT_AREA);
  });

  it('does not treat a rendered zero as no child', () => {
    // "0" is text: the button is not an icon-only control.
    render(<Button icon={Pencil}>{0}</Button>);
    expect(screen.getByRole('button', { name: '0' }).className)
      .not.toContain('after:size-11');
  });
});
