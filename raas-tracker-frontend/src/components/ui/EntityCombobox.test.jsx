import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { useState } from 'react';
import EntityCombobox from './EntityCombobox';

const OPTIONS = [
  { value: '1', label: 'Acme' },
  { value: '2', label: 'Beta' },
  { value: '3', label: 'Gamma', disabled: true },
];

function Controlled({ onChange, initial = '' }) {
  const [value, setValue] = useState(initial);
  return (
    <EntityCombobox
      aria-label="Company"
      options={OPTIONS}
      value={value}
      onChange={(next) => {
        setValue(next);
        onChange?.(next);
      }}
    />
  );
}

describe('EntityCombobox', () => {
  it('filters options by substring and lists all on focus', () => {
    render(<Controlled />);
    const input = screen.getByLabelText('Company');
    fireEvent.focus(input);
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual([
      'Acme', 'Beta', 'Gamma',
    ]);
    fireEvent.change(input, { target: { value: 'bet' } });
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual(['Beta']);
  });

  it('commits a value when an option is clicked', () => {
    const onChange = vi.fn();
    render(<Controlled onChange={onChange} />);
    const input = screen.getByLabelText('Company');
    fireEvent.focus(input);
    fireEvent.click(screen.getByRole('option', { name: 'Beta' }));
    expect(onChange).toHaveBeenCalledWith('2');
    expect(input).toHaveValue('Beta');
  });

  it('commits an exact label typed in full', () => {
    const onChange = vi.fn();
    render(<Controlled onChange={onChange} />);
    fireEvent.change(screen.getByLabelText('Company'), { target: { value: 'Beta' } });
    expect(onChange).toHaveBeenCalledWith('2');
  });

  it('does not auto-commit when two options share the label', () => {
    const onChange = vi.fn();
    render(
      <EntityCombobox
        aria-label="Company"
        options={[{ value: '1', label: 'Acme' }, { value: '2', label: 'Acme' }]}
        value=""
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByLabelText('Company'), { target: { value: 'Acme' } });
    expect(onChange).not.toHaveBeenCalled();
  });

  it('skips disabled options and refuses to commit them', () => {
    const onChange = vi.fn();
    render(<Controlled onChange={onChange} />);
    const input = screen.getByLabelText('Company');
    fireEvent.focus(input);
    fireEvent.click(screen.getByRole('option', { name: 'Gamma' }));
    expect(onChange).not.toHaveBeenCalled();

    fireEvent.focus(input);
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onChange).toHaveBeenLastCalledWith('2');
  });

  it('commits the active option on Enter', () => {
    const onChange = vi.fn();
    render(<Controlled onChange={onChange} />);
    const input = screen.getByLabelText('Company');
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: 'Bet' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onChange).toHaveBeenCalledWith('2');
  });

  it('clears the value and shows a hint for unmatched text', () => {
    const onChange = vi.fn();
    render(<Controlled onChange={onChange} initial="1" />);
    const input = screen.getByLabelText('Company');
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: 'Zzz' } });
    expect(onChange).toHaveBeenLastCalledWith('');
    fireEvent.blur(input);
    expect(screen.getByRole('status').textContent).toContain('No matching record');
    expect(input).toHaveValue('Zzz');
    expect(input).toHaveAttribute('aria-invalid', 'true');
  });

  it('reverts to the selected label on Escape', () => {
    render(<Controlled initial="1" />);
    const input = screen.getByLabelText('Company');
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: 'Be' } });
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(input).toHaveValue('Acme');
  });

  it('shows the selected label while closed', () => {
    render(<EntityCombobox aria-label="Company" options={OPTIONS} value="2" onChange={() => {}} />);
    expect(screen.getByLabelText('Company')).toHaveValue('Beta');
  });

  it('follows a parent value change while focused', () => {
    const { rerender } = render(
      <EntityCombobox aria-label="Company" options={OPTIONS} value="1" onChange={() => {}} />,
    );
    const input = screen.getByLabelText('Company');
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: 'Acme' } });
    rerender(<EntityCombobox aria-label="Company" options={OPTIONS} value="2" onChange={() => {}} />);
    expect(input).toHaveValue('Beta');
    expect(screen.queryByRole('listbox')).toBeNull();
  });
});
