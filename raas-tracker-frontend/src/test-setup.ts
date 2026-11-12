// Test setup file for vitest
import '@testing-library/jest-dom';
import { configure } from '@testing-library/react';

// Testing Library's `asyncUtilTimeout` bounds how long a single async
// assertion - a `waitFor` retry loop - keeps polling before it gives up. Its
// default is 1000 ms. GitHub's runners are slower than a dev machine and run
// the whole suite in parallel, so a legitimate state change - a sale detail
// resolving, a combobox opening its list - can arrive after that second and
// fail a test whose app behaviour is correct; the invoice-target combobox
// lost exactly that race on CI.
//
// This budget is independent of vitest's per-test timeout (see
// vitest.config.ts): one bounds a single async assertion, the other bounds the
// whole test. The assertions themselves are untouched - a state change that
// never arrives still fails.
configure({ asyncUtilTimeout: 5000 });
