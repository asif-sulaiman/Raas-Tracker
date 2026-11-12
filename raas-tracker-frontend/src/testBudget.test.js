// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { waitFor } from '@testing-library/react';

// Testing Library's async-assertion budget used to be its 1000 ms default.
// GitHub's runners are slower than a dev machine and run the whole frontend
// suite in parallel, so a legitimate state change - a sale detail resolving,
// a combobox opening its list - can miss that window and fail a test whose
// app behaviour is correct. The invoice-target combobox lost exactly that
// race on CI.
//
// The budget is pinned here so it cannot be lowered back without this test
// going red again.
describe('async assertion budget', () => {
  it('survives a state change that takes longer than the old default', async () => {
    let settled = false;
    setTimeout(() => { settled = true; }, 1500);

    await waitFor(() => expect(settled).toBe(true));
  });
});
