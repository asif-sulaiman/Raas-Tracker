// Test setup file for vitest
import '@testing-library/jest-dom';
import { configure } from '@testing-library/react';

// Testing Library's async-assertion budget is 1000 ms by default. GitHub's
// runners are slower than a dev machine and run the whole suite in parallel,
// so a legitimate state change - a sale detail resolving, a combobox opening
// its list - can miss that window and fail a test whose app behaviour is
// correct; the invoice-target combobox lost exactly that race on CI.
//
// The budget is raised to match the per-test timeout below. The assertions
// themselves are untouched: a state change that never arrives still fails.
configure({ asyncUtilTimeout: 5000 });
