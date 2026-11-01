import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import SuggestionInput from './SuggestionInput';

describe('SuggestionInput', () => {
  it('renders every option as a datalist suggestion', () => {
    render(<SuggestionInput options={['KG', 'L', 'DRUM']} value="" onChange={() => {}} aria-label="Unit" />);
    const input = screen.getByLabelText('Unit');
    const listId = input.getAttribute('list');
    const values = Array.from(document.querySelectorAll(`#${CSS.escape(listId)} option`)).map(
      (o) => o.getAttribute('value'),
    );
    expect(values).toEqual(['KG', 'L', 'DRUM']);
  });

  it('accepts text that is not in the list', () => {
    const onChange = vi.fn();
    render(<SuggestionInput options={['KG']} value="" onChange={onChange} aria-label="Unit" />);
    fireEvent.change(screen.getByLabelText('Unit'), { target: { value: 'PALLET' } });
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it('gives each instance its own datalist', () => {
    render(
      <>
        <SuggestionInput options={['KG']} value="" onChange={() => {}} aria-label="First" />
        <SuggestionInput options={['KG']} value="" onChange={() => {}} aria-label="Second" />
      </>,
    );
    const first = screen.getByLabelText('First').getAttribute('list');
    const second = screen.getByLabelText('Second').getAttribute('list');
    expect(first).not.toBe(second);
  });

  it('forwards input props untouched', () => {
    render(
      <SuggestionInput
        options={['KG']}
        value="KG"
        onChange={() => {}}
        className="row-input"
        placeholder="Unit"
        maxLength={120}
        aria-label="Unit"
      />,
    );
    const input = screen.getByLabelText('Unit');
    expect(input).toHaveValue('KG');
    expect(input).toHaveClass('row-input');
    expect(input).toHaveAttribute('placeholder', 'Unit');
    expect(input).toHaveAttribute('maxlength', '120');
  });
});
