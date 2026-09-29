// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import CommercialCalendarNavigator from './CommercialCalendarNavigator.jsx';

// Mock lucide-react icons
vi.mock('lucide-react', () => ({
  ChevronLeft: () => <svg data-testid="chevron-left" />,
  ChevronRight: () => <svg data-testid="chevron-right" />,
  Calendar: () => <svg data-testid="calendar" />,
  Minus: () => <svg data-testid="minus" />,
  Plus: () => <svg data-testid="plus" />,
}));

// Mock Button component
vi.mock('../ui/Button', () => ({
  default: ({ children, onClick, disabled, variant, size, className, 'aria-label': ariaLabel, ...props }) => (
    <button
      onClick={onClick}
      disabled={disabled}
      className={className}
      aria-label={ariaLabel}
      data-testid={ariaLabel?.toLowerCase().replace(/\s+/g, '-') || 'button'}
      {...props}
    >
      {children}
    </button>
  ),
}));

function renderNavigator(props = {}, initialEntries = ['/']) {
  const defaultProps = {
    groupBy: 'month',
    dateAnchor: 'pi_date',
    dateFrom: '',
    dateTo: '',
    onFiltersChange: vi.fn(),
    periodStart: '2025-06-01',
    periodEnd: '2025-06-30',
    isLoading: false,
    ...props,
  };

  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <CommercialCalendarNavigator {...defaultProps} />
    </MemoryRouter>
  );
}

describe('CommercialCalendarNavigator', () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  describe('Period Navigation - Month', () => {
    it('renders period label correctly for month view', () => {
      renderNavigator({ groupBy: 'month', periodStart: '2025-06-01', periodEnd: '2025-06-30' });
      expect(screen.getByText('Month View')).toBeInTheDocument();
      expect(screen.getByText('June 2025')).toBeInTheDocument();
    });

    it('calls onFiltersChange with correct dates when prev button clicked', async () => {
      const onFiltersChange = vi.fn();
      renderNavigator({ groupBy: 'month', periodStart: '2025-06-01', periodEnd: '2025-06-30', onFiltersChange });
      const prevButton = screen.getByLabelText('Previous period');
      fireEvent.click(prevButton);
      
      await waitFor(() => {
        expect(onFiltersChange).toHaveBeenCalledWith(
          expect.objectContaining({
            date_from: '2025-05-01',
            date_to: '2025-05-31',
            page: 1,
          })
        );
      });
    });

    it('calls onFiltersChange with correct dates when next button clicked', async () => {
      const onFiltersChange = vi.fn();
      renderNavigator({ groupBy: 'month', periodStart: '2025-06-01', periodEnd: '2025-06-30', onFiltersChange });
      const nextButton = screen.getByLabelText('Next period');
      fireEvent.click(nextButton);
      
      await waitFor(() => {
        expect(onFiltersChange).toHaveBeenCalledWith(
          expect.objectContaining({
            date_from: '2025-07-01',
            date_to: '2025-07-31',
            page: 1,
          })
        );
      });
    });

    it('handles year boundary when navigating previous from January', async () => {
      const onFiltersChange = vi.fn();
      renderNavigator({ groupBy: 'month', periodStart: '2025-01-01', periodEnd: '2025-01-31', onFiltersChange });
      const prevButton = screen.getByLabelText('Previous period');
      fireEvent.click(prevButton);
      
      await waitFor(() => {
        expect(onFiltersChange).toHaveBeenCalledWith(
          expect.objectContaining({
            date_from: '2024-12-01',
            date_to: '2024-12-31',
            page: 1,
          })
        );
      });
    });

    it('handles year boundary when navigating next from December', async () => {
      const onFiltersChange = vi.fn();
      renderNavigator({ groupBy: 'month', periodStart: '2025-12-01', periodEnd: '2025-12-31', onFiltersChange });
      const nextButton = screen.getByLabelText('Next period');
      fireEvent.click(nextButton);
      
      await waitFor(() => {
        expect(onFiltersChange).toHaveBeenCalledWith(
          expect.objectContaining({
            date_from: '2026-01-01',
            date_to: '2026-01-31',
            page: 1,
          })
        );
      });
    });
  });

  describe('Period Navigation - Week', () => {
    it('renders period label correctly for week view', () => {
      // Week starting Monday June 2, 2025 to Sunday June 8, 2025
      renderNavigator({ groupBy: 'week', periodStart: '2025-06-02', periodEnd: '2025-06-08' });
      expect(screen.getByText('Week View')).toBeInTheDocument();
      expect(screen.getByText('Jun 2 – Jun 8')).toBeInTheDocument();
    });

    it('calls onFiltersChange with correct dates when prev button clicked', async () => {
      const onFiltersChange = vi.fn();
      renderNavigator({ groupBy: 'week', periodStart: '2025-06-02', periodEnd: '2025-06-08', onFiltersChange });
      const prevButton = screen.getByLabelText('Previous period');
      fireEvent.click(prevButton);
      
      await waitFor(() => {
        expect(onFiltersChange).toHaveBeenCalledWith(
          expect.objectContaining({
            date_from: '2025-05-26',
            date_to: '2025-06-01',
            page: 1,
          })
        );
      });
    });

    it('calls onFiltersChange with correct dates when next button clicked', async () => {
      const onFiltersChange = vi.fn();
      renderNavigator({ groupBy: 'week', periodStart: '2025-06-02', periodEnd: '2025-06-08', onFiltersChange });
      const nextButton = screen.getByLabelText('Next period');
      fireEvent.click(nextButton);
      
      await waitFor(() => {
        expect(onFiltersChange).toHaveBeenCalledWith(
          expect.objectContaining({
            date_from: '2025-06-09',
            date_to: '2025-06-15',
            page: 1,
          })
        );
      });
    });

    it('handles year boundary for week navigation', async () => {
      const onFiltersChange = vi.fn();
      // Last week of 2025: Dec 29, 2025 (Mon) - Jan 4, 2026 (Sun)
      renderNavigator({ groupBy: 'week', periodStart: '2025-12-29', periodEnd: '2026-01-04', onFiltersChange });
      const nextButton = screen.getByLabelText('Next period');
      fireEvent.click(nextButton);
      
      await waitFor(() => {
        expect(onFiltersChange).toHaveBeenCalledWith(
          expect.objectContaining({
            date_from: '2026-01-05',
            date_to: '2026-01-11',
            page: 1,
          })
        );
      });
    });
  });

  describe('Period Navigation - Year', () => {
    it('renders period label correctly for year view', () => {
      renderNavigator({ groupBy: 'year', periodStart: '2025-01-01', periodEnd: '2025-12-31' });
      expect(screen.getByText('Year View')).toBeInTheDocument();
      expect(screen.getByText('2025')).toBeInTheDocument();
    });

    it('calls onFiltersChange with correct dates when prev button clicked', async () => {
      const onFiltersChange = vi.fn();
      renderNavigator({ groupBy: 'year', periodStart: '2025-01-01', periodEnd: '2025-12-31', onFiltersChange });
      const prevButton = screen.getByLabelText('Previous period');
      fireEvent.click(prevButton);
      
      await waitFor(() => {
        expect(onFiltersChange).toHaveBeenCalledWith(
          expect.objectContaining({
            date_from: '2024-01-01',
            date_to: '2024-12-31',
            page: 1,
          })
        );
      });
    });

    it('calls onFiltersChange with correct dates when next button clicked', async () => {
      const onFiltersChange = vi.fn();
      renderNavigator({ groupBy: 'year', periodStart: '2025-01-01', periodEnd: '2025-12-31', onFiltersChange });
      const nextButton = screen.getByLabelText('Next period');
      fireEvent.click(nextButton);
      
      await waitFor(() => {
        expect(onFiltersChange).toHaveBeenCalledWith(
          expect.objectContaining({
            date_from: '2026-01-01',
            date_to: '2026-12-31',
            page: 1,
          })
        );
      });
    });
  });

  describe('Date Preset Selection', () => {
    it('shows all preset options in dropdown', () => {
      renderNavigator();
      const select = screen.getByLabelText('Quick Preset');
      expect(select).toBeInTheDocument();
      
      const options = screen.getAllByRole('option');
      expect(options.map(o => o.textContent)).toContain('Custom Range');
      expect(options.map(o => o.textContent)).toContain('This Month');
      expect(options.map(o => o.textContent)).toContain('Last Month');
      expect(options.map(o => o.textContent)).toContain('This Quarter');
      expect(options.map(o => o.textContent)).toContain('Last Quarter');
      expect(options.map(o => o.textContent)).toContain('This Year');
      expect(options.map(o => o.textContent)).toContain('Last Year');
    });

    it('calls onFiltersChange when preset is selected', async () => {
      const onFiltersChange = vi.fn();
      renderNavigator({ onFiltersChange });
      
      const select = screen.getByLabelText('Quick Preset');
      fireEvent.change(select, { target: { value: 'This Month' } });
      
      await waitFor(() => {
        expect(onFiltersChange).toHaveBeenCalledWith(
          expect.objectContaining({
            date_preset: 'This Month',
            date_from: expect.any(String),
            date_to: expect.any(String),
            page: 1,
          })
        );
      });
    });

    it('clears dates when Custom Range is selected', async () => {
      const onFiltersChange = vi.fn();
      renderNavigator({ 
        onFiltersChange,
        dateFrom: '2025-01-01',
        dateTo: '2025-01-31',
      }, ['/?date_preset=This Month&date_from=2025-01-01&date_to=2025-01-31']);
      
      const select = screen.getByLabelText('Quick Preset');
      fireEvent.change(select, { target: { value: '' } });
      
      await waitFor(() => {
        expect(onFiltersChange).toHaveBeenCalledWith(
          expect.objectContaining({
            date_preset: '',
            date_from: '',
            date_to: '',
            page: 1,
          })
        );
      });
    });
  });

  describe('Date Anchor Selection', () => {
    it('shows all date anchor options', () => {
      renderNavigator();
      const select = screen.getByLabelText('Date Anchor');
      expect(select).toBeInTheDocument();
      
      const options = screen.getAllByRole('option');
      expect(options.map(o => o.textContent)).toContain('PI Date');
      expect(options.map(o => o.textContent)).toContain('LC Date');
      expect(options.map(o => o.textContent)).toContain('Shipment Date');
      expect(options.map(o => o.textContent)).toContain('Receive Date');
      expect(options.map(o => o.textContent)).toContain('Maturity Date');
    });

    it('calls onFiltersChange when date anchor changes', async () => {
      const onFiltersChange = vi.fn();
      renderNavigator({ onFiltersChange, dateAnchor: 'pi_date' });
      
      const select = screen.getByLabelText('Date Anchor');
      fireEvent.change(select, { target: { value: 'lc_date' } });
      
      await waitFor(() => {
        expect(onFiltersChange).toHaveBeenCalledWith(
          expect.objectContaining({
            date_anchor: 'lc_date',
            page: 1,
          })
        );
      });
    });
  });

  describe('Custom Date Inputs', () => {
    it('calls onFiltersChange when date from changes', async () => {
      const onFiltersChange = vi.fn();
      renderNavigator({ onFiltersChange, dateFrom: '' });
      
      const input = screen.getByLabelText('From');
      fireEvent.change(input, { target: { value: '2025-07-01' } });
      
      await waitFor(() => {
        expect(onFiltersChange).toHaveBeenCalledWith(
          expect.objectContaining({
            date_from: '2025-07-01',
            date_preset: '',
            page: 1,
          })
        );
      });
    });

    it('calls onFiltersChange when date to changes', async () => {
      const onFiltersChange = vi.fn();
      renderNavigator({ onFiltersChange, dateTo: '' });
      
      const input = screen.getByLabelText('To');
      fireEvent.change(input, { target: { value: '2025-07-31' } });
      
      await waitFor(() => {
        expect(onFiltersChange).toHaveBeenCalledWith(
          expect.objectContaining({
            date_to: '2025-07-31',
            date_preset: '',
            page: 1,
          })
        );
      });
    });

    it('clears preset when custom date is entered', async () => {
      const onFiltersChange = vi.fn();
      renderNavigator({ 
        onFiltersChange, 
        dateFrom: '2025-06-01',
        dateTo: '2025-06-30',
      }, ['/?date_preset=This Month&date_from=2025-06-01&date_to=2025-06-30']);
      
      const input = screen.getByLabelText('From');
      fireEvent.change(input, { target: { value: '2025-07-01' } });
      
      await waitFor(() => {
        expect(onFiltersChange).toHaveBeenCalledWith(
          expect.objectContaining({
            date_preset: '',
          })
        );
      });
    });
  });

  describe('Clear Dates Button', () => {
    it('clears all dates and preset when clear button clicked', async () => {
      const onFiltersChange = vi.fn();
      renderNavigator({ 
        onFiltersChange, 
        dateFrom: '2025-06-01',
        dateTo: '2025-06-30',
      }, ['/?date_preset=This Month&date_from=2025-06-01&date_to=2025-06-30']);
      
      const clearButton = screen.getByText('Clear Dates');
      fireEvent.click(clearButton);
      
      await waitFor(() => {
        expect(onFiltersChange).toHaveBeenCalledWith(
          expect.objectContaining({
            date_from: '',
            date_to: '',
            date_preset: '',
            page: 1,
          })
        );
      });
    });
  });

  describe('Loading State', () => {
    it('disables navigation buttons when loading', () => {
      renderNavigator({ isLoading: true });
      const prevButton = screen.getByLabelText('Previous period');
      const nextButton = screen.getByLabelText('Next period');
      
      expect(prevButton).toHaveAttribute('disabled');
      expect(nextButton).toHaveAttribute('disabled');
    });
  });

  describe('Edge Cases', () => {
    it('shows "No period selected" when periodStart/periodEnd not provided', () => {
      renderNavigator({ periodStart: '', periodEnd: '' });
      expect(screen.getByText('No period selected')).toBeInTheDocument();
    });

    it('handles week starting on Sunday correctly (ISO week Monday start)', () => {
      // June 15, 2025 is a Sunday. ISO week starts Monday June 9, ends Sunday June 15.
      renderNavigator({ groupBy: 'week', periodStart: '2025-06-09', periodEnd: '2025-06-15' });
      expect(screen.getByText('Jun 9 \u2013 Jun 15')).toBeInTheDocument();
    });

    it('does not navigate when periodStart or periodEnd is missing', () => {
      const onFiltersChange = vi.fn();
      renderNavigator({ onFiltersChange, periodStart: '', periodEnd: '2025-06-30' });
      
      const prevButton = screen.getByLabelText('Previous period');
      fireEvent.click(prevButton);
      
      expect(onFiltersChange).not.toHaveBeenCalled();
    });
  });
});