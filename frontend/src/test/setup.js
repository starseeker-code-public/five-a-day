// Vitest setup — runs before every test file (vite.config.js `test.setupFiles`).
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// Testing Library only auto-cleans when it detects a global afterEach from a
// framework it recognises; doing it explicitly means a component that attaches
// a scroll or resize listener cannot leak into the next test.
afterEach(() => {
  cleanup();
});

// jsdom implements neither, and several components call them on mount:
// ScrollToTop uses window.scrollTo, Reveal uses IntersectionObserver. Without
// these every page test fails on the observer rather than on anything real.
window.scrollTo = vi.fn();

class IntersectionObserverStub {
  constructor(callback) {
    this.callback = callback;
  }
  // Report the element as visible straight away. Reveal renders its children
  // only once observed, so a no-op stub would make every page look empty and
  // the tests would assert against a blank document.
  observe(element) {
    this.callback([{ isIntersecting: true, target: element }], this);
  }
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return [];
  }
}
window.IntersectionObserver = IntersectionObserverStub;
// Components that capture the constructor off the global rather than off
// window still find it. `globalThis`, not Node's `global`.
globalThis.IntersectionObserver = IntersectionObserverStub;

// jsdom does not implement matchMedia, which Tailwind-adjacent code and some
// hooks probe for.
window.matchMedia =
  window.matchMedia ||
  ((query) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
