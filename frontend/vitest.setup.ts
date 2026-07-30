/**
 * Test setup: DOM matchers, and automatic cleanup between tests.
 *
 * `cleanup` is called explicitly rather than relying on RTL's auto-cleanup,
 * which only registers itself when a global `afterEach` exists at import time -
 * a condition that is easy to lose and fails as cross-test pollution rather
 * than as an error.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});
