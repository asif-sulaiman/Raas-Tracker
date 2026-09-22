// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import ErrorBoundary from './ErrorBoundary.jsx';

afterEach(() => cleanup());

function Boom() {
  throw new Error('kaboom');
}

let failOnce = true;
function Flaky() {
  if (failOnce) throw new Error('first render only');
  return <p>recovered</p>;
}

function silenceConsole() {
  return vi.spyOn(console, 'error').mockImplementation(() => {});
}

describe('ErrorBoundary', () => {
  it('getDerivedStateFromError flags the error without DOM', () => {
    const err = new Error('x');
    expect(ErrorBoundary.getDerivedStateFromError(err)).toEqual({ hasError: true, error: err });
  });

  it('renders children when healthy', () => {
    render(
      <ErrorBoundary>
        <p>fine</p>
      </ErrorBoundary>
    );
    expect(screen.getByText('fine')).toBeTruthy();
  });

  it('shows the fallback panel on a render crash', () => {
    const spy = silenceConsole();
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>
    );
    expect(screen.getByText('Something went wrong rendering this page')).toBeTruthy();
    expect(screen.getByText('Try again')).toBeTruthy();
    spy.mockRestore();
  });

  it('recovers via Try again once the cause is gone', () => {
    const spy = silenceConsole();
    failOnce = true;
    render(
      <ErrorBoundary>
        <Flaky />
      </ErrorBoundary>
    );
    expect(screen.getByText('Try again')).toBeTruthy();
    failOnce = false;
    fireEvent.click(screen.getByText('Try again'));
    expect(screen.getByText('recovered')).toBeTruthy();
    spy.mockRestore();
  });

  it('renders a custom global fallback when provided', () => {
    const spy = silenceConsole();
    render(
      <ErrorBoundary fallback={<p>global safety net</p>}>
        <Boom />
      </ErrorBoundary>
    );
    expect(screen.getByText('global safety net')).toBeTruthy();
    spy.mockRestore();
  });
});
