import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import ProductInput from './ProductInput';
import { PRODUCT_CATALOG, PRODUCT_NAMES } from '../../data/products';

describe('ProductInput', () => {
  it('offers the full 2026 catalog as a datalist and keeps free text editable', () => {
    const onChange = vi.fn();
    render(
      <ProductInput value="" onChange={onChange} placeholder="Product name" aria-label="Product name" />,
    );

    const input = screen.getByLabelText('Product name');
    const listId = input.getAttribute('list');
    expect(listId).toBeTruthy();
    expect(document.querySelectorAll(`#${CSS.escape(listId)} option`)).toHaveLength(70);

    fireEvent.change(input, { target: { value: 'Unlisted Product 99' } });
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it('contains the catalog names as option values', () => {
    render(<ProductInput value="" onChange={() => {}} aria-label="Product" />);
    const values = Array.from(document.querySelectorAll('datalist option')).map(
      (o) => o.getAttribute('value'),
    );

    expect(values).toEqual(PRODUCT_NAMES);
    expect(values).toContain('Cottonese BT 11');
    expect(values).toContain('Ecoclean RM');
  });

  it('catalog holds 70 unique products with a category and HS code', () => {
    expect(PRODUCT_CATALOG).toHaveLength(70);
    expect(new Set(PRODUCT_NAMES).size).toBe(70);
    for (const product of PRODUCT_CATALOG) {
      expect(product.category).toBeTruthy();
      expect(product.hs_code).toMatch(/^\d{4}(?:\.\d{2,4}){1,3}$/);
    }
  });

  it('gives each instance its own datalist', () => {
    render(
      <>
        <ProductInput value="" onChange={() => {}} aria-label="First" />
        <ProductInput value="" onChange={() => {}} aria-label="Second" />
      </>,
    );

    const first = screen.getByLabelText('First').getAttribute('list');
    const second = screen.getByLabelText('Second').getAttribute('list');
    expect(first).not.toBe(second);
  });

  it('still behaves like a text input (className, value, placeholder)', () => {
    render(
      <ProductInput
        value="Huefix AR"
        onChange={() => {}}
        className="row-input"
        placeholder="Product name"
        aria-label="Product"
      />,
    );
    const input = screen.getByLabelText('Product');
    expect(input).toHaveValue('Huefix AR');
    expect(input).toHaveClass('row-input');
    expect(input).toHaveAttribute('placeholder', 'Product name');
  });
});
