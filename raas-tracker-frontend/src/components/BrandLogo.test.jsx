// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import React from 'react';
import { render, screen } from '@testing-library/react';
import BrandLogo from './BrandLogo';

describe('BrandLogo', () => {
  it('renders the company logo image', () => {
    render(<BrandLogo />);
    const img = screen.getByAltText('RAAS Biotech logo');
    expect(img.tagName).toBe('IMG');
    expect(img.getAttribute('src')).toBe('/raas-logo.png');
  });

  it('defaults to a compact size and allows overriding it', () => {
    const { container } = render(<BrandLogo size="h-16 w-16" />);
    const tile = container.firstChild;
    expect(tile.className).toContain('h-16');
    expect(tile.className).toContain('w-16');
  });
});
