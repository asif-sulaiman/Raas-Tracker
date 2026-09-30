// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import CommercialFilters from './CommercialFilters.jsx';

// Mock the datePresets utility
vi.mock('../../utils/datePresets', () => ({
  DATE_PRESETS: {
    'This Month': vi.fn(() => ({ from: '2025-06-01', to: '2025-06-15' })),
    'Last Month': vi.fn(() => ({ from: '2025-05-01', to: '2025-05-31' })),
  },
  dateAnchorOptions: [
    { value: 'pi_date', label: 'PI Date' },
    { value: 'lc_date', label: 'LC Date' },
  ],
}));

const mockCompanies = [
  { id: 1, name: 'Company A' },
  { id: 2, name: 'Company B' },
];

function renderWithRouter(ui, { initialPath = '/reports' } = {}) {
  return render(<MemoryRouter initialEntries={[initialPath]}>{ui}</MemoryRouter>);
}

describe('CommercialFilters', () => {
  const mockOnFiltersChange = vi.fn();

  beforeEach(() => {
    mockOnFiltersChange.mockClear();
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2025-06-15T12:00:00Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
    cleanup();
  });

  it('renders all filter fields', () => {
    renderWithRouter(<CommercialFilters companies={mockCompanies} onFiltersChange={mockOnFiltersChange} />);

    expect(screen.getByPlaceholderText('Quick Search: PI No / Customer')).toBeTruthy();
    expect(screen.getByLabelText('Date Anchor')).toBeTruthy();
    expect(screen.getByLabelText('Quick Range')).toBeTruthy();
    expect(screen.getByLabelText('From')).toBeTruthy();
    expect(screen.getByLabelText('To')).toBeTruthy();
    expect(screen.getByLabelText('Stage')).toBeTruthy();
    expect(screen.getByLabelText('Payment Status')).toBeTruthy();
    expect(screen.getByLabelText('Company')).toBeTruthy();
    expect(screen.getByLabelText('Group By')).toBeTruthy();
    expect(screen.getByRole('button', { name: /clear/i })).toBeTruthy();
  });

  it('populates company dropdown with provided companies', () => {
    renderWithRouter(<CommercialFilters companies={mockCompanies} onFiltersChange={mockOnFiltersChange} />);

    const companySelect = screen.getByLabelText('Company');
    expect(companySelect.value).toBe('');
    expect(screen.getByRole('option', { name: 'All Companies' })).toBeTruthy();
    expect(screen.getByRole('option', { name: 'Company A' })).toBeTruthy();
    expect(screen.getByRole('option', { name: 'Company B' })).toBeTruthy();
  });

  it('updates URL and calls onFiltersChange when quick search changes', () => {
    renderWithRouter(
      <CommercialFilters companies={mockCompanies} onFiltersChange={mockOnFiltersChange} />,
      { initialPath: '/reports' }
    );

    const searchInput = screen.getByPlaceholderText('Quick Search: PI No / Customer');
    fireEvent.change(searchInput, { target: { value: 'PI-123' } });

    // One term is sent as `q` (server ORs it across PI / customer / product) —
    // never duplicated into customer_name AND product_name, which the server
    // would AND together and almost always match nothing.
    expect(mockOnFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({
        q: 'PI-123',
        page: 1,
      })
    );
    expect(mockOnFiltersChange).not.toHaveBeenCalledWith(
      expect.objectContaining({ customer_name: 'PI-123' })
    );
  });

  it('updates URL when date anchor changes', () => {
    renderWithRouter(
      <CommercialFilters companies={mockCompanies} onFiltersChange={mockOnFiltersChange} />,
      { initialPath: '/reports' }
    );

    const anchorSelect = screen.getByLabelText('Date Anchor');
    fireEvent.change(anchorSelect, { target: { value: 'lc_date' } });

    expect(mockOnFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({
        date_anchor: 'lc_date',
        page: 1,
      })
    );
  });

  it('applies date preset and sets from/to dates', () => {
    renderWithRouter(
      <CommercialFilters companies={mockCompanies} onFiltersChange={mockOnFiltersChange} />,
      { initialPath: '/reports' }
    );

    const presetSelect = screen.getByLabelText('Quick Range');
    fireEvent.change(presetSelect, { target: { value: 'This Month' } });

    expect(mockOnFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({
        date_preset: 'This Month',
        date_from: '2025-06-01',
        date_to: '2025-06-15',
        page: 1,
      })
    );
  });

  it('clears from/to when preset is set to empty', () => {
    renderWithRouter(
      <CommercialFilters companies={mockCompanies} onFiltersChange={mockOnFiltersChange} />,
      { initialPath: '/reports?date_preset=This Month&date_from=2025-06-01&date_to=2025-06-15' }
    );

    const presetSelect = screen.getByLabelText('Quick Range');
    fireEvent.change(presetSelect, { target: { value: '' } });

    expect(mockOnFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({
        date_preset: '',
        date_from: '',
        date_to: '',
        page: 1,
      })
    );
  });

  it('clears preset when custom from date is entered', () => {
    renderWithRouter(
      <CommercialFilters companies={mockCompanies} onFiltersChange={mockOnFiltersChange} />,
      { initialPath: '/reports?date_preset=This Month' }
    );

    const fromInput = screen.getByLabelText('From');
    fireEvent.change(fromInput, { target: { value: '2025-01-01' } });

    expect(mockOnFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({
        date_from: '2025-01-01',
        date_preset: '',
        page: 1,
      })
    );
  });

  it('clears preset when custom to date is entered', () => {
    renderWithRouter(
      <CommercialFilters companies={mockCompanies} onFiltersChange={mockOnFiltersChange} />,
      { initialPath: '/reports?date_preset=This Month' }
    );

    const toInput = screen.getByLabelText('To');
    fireEvent.change(toInput, { target: { value: '2025-12-31' } });

    expect(mockOnFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({
        date_to: '2025-12-31',
        date_preset: '',
        page: 1,
      })
    );
  });

  it('updates URL when stage changes', () => {
    renderWithRouter(
      <CommercialFilters companies={mockCompanies} onFiltersChange={mockOnFiltersChange} />,
      { initialPath: '/reports' }
    );

    const stageSelect = screen.getByLabelText('Stage');
    fireEvent.change(stageSelect, { target: { value: 'lc_received' } });

    expect(mockOnFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({
        stage: 'lc_received',
        page: 1,
      })
    );
  });

  it('updates URL when payment status changes', () => {
    renderWithRouter(
      <CommercialFilters companies={mockCompanies} onFiltersChange={mockOnFiltersChange} />,
      { initialPath: '/reports' }
    );

    const statusSelect = screen.getByLabelText('Payment Status');
    fireEvent.change(statusSelect, { target: { value: 'Paid' } });

    expect(mockOnFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({
        payment_status: 'Paid',
        page: 1,
      })
    );
  });

  it('updates URL when company changes', () => {
    renderWithRouter(
      <CommercialFilters companies={mockCompanies} onFiltersChange={mockOnFiltersChange} />,
      { initialPath: '/reports' }
    );

    const companySelect = screen.getByLabelText('Company');
    fireEvent.change(companySelect, { target: { value: '1' } });

    expect(mockOnFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({
        company_id: '1',
        page: 1,
      })
    );
  });

  it('updates URL when group by changes', () => {
    renderWithRouter(
      <CommercialFilters companies={mockCompanies} onFiltersChange={mockOnFiltersChange} />,
      { initialPath: '/reports' }
    );

    const groupSelect = screen.getByLabelText('Group By');
    fireEvent.change(groupSelect, { target: { value: 'month' } });

    expect(mockOnFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({
        group_by: 'month',
        page: 1,
      })
    );
  });

  it('clears all filters on Clear button click', () => {
    renderWithRouter(
      <CommercialFilters companies={mockCompanies} onFiltersChange={mockOnFiltersChange} />,
      {
        initialPath:
          '/reports?date_anchor=lc_date&date_from=2025-01-01&date_to=2025-12-31&date_preset=This Year&q=test&customer_name=test&product_name=test&company_id=1&stage=pi_issued&payment_status=Paid&group_by=month&page=3',
      }
    );

    const clearButton = screen.getByRole('button', { name: /clear/i });
    fireEvent.click(clearButton);

    expect(mockOnFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({
        date_anchor: 'pi_date',
        date_from: '',
        date_to: '',
        date_preset: '',
        q: '',
        customer_name: '',
        product_name: '',
        company_id: '',
        stage: '',
        payment_status: '',
        page: 1,
      })
    );
  });

  it('preserves group_by when clearing all filters', () => {
    renderWithRouter(
      <CommercialFilters companies={mockCompanies} onFiltersChange={mockOnFiltersChange} />,
      {
        initialPath: '/reports?group_by=month',
      }
    );

    const clearButton = screen.getByRole('button', { name: /clear/i });
    fireEvent.click(clearButton);

    expect(mockOnFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({
        group_by: 'month',
      })
    );
  });

  it('reads initial filters from URL', () => {
    renderWithRouter(
      <CommercialFilters companies={mockCompanies} onFiltersChange={mockOnFiltersChange} />,
      {
        initialPath: '/reports?date_anchor=lc_date&stage=pi_issued&payment_status=Paid&company_id=2',
      }
    );

    expect(screen.getByLabelText('Date Anchor').value).toBe('lc_date');
    expect(screen.getByLabelText('Stage').value).toBe('pi_issued');
    expect(screen.getByLabelText('Payment Status').value).toBe('Paid');
    expect(screen.getByLabelText('Company').value).toBe('2');
  });

  it('defaults to pi_date when no date_anchor in URL', () => {
    renderWithRouter(
      <CommercialFilters companies={mockCompanies} onFiltersChange={mockOnFiltersChange} />,
      { initialPath: '/reports' }
    );

    expect(screen.getByLabelText('Date Anchor').value).toBe('pi_date');
  });

  it('defaults to none for group_by when not in URL', () => {
    renderWithRouter(
      <CommercialFilters companies={mockCompanies} onFiltersChange={mockOnFiltersChange} />,
      { initialPath: '/reports' }
    );

    expect(screen.getByLabelText('Group By').value).toBe('none');
  });

  it('resets page to 1 when any filter changes', () => {
    renderWithRouter(
      <CommercialFilters companies={mockCompanies} onFiltersChange={mockOnFiltersChange} />,
      { initialPath: '/reports?page=5' }
    );

    const searchInput = screen.getByPlaceholderText('Quick Search: PI No / Customer');
    fireEvent.change(searchInput, { target: { value: 'test' } });

    expect(mockOnFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({
        page: 1,
      })
    );
  });
});