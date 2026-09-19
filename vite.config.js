import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// The React sources live in frontend/ while this config, package.json and the
// lockfile sit at the repo root beside pyproject.toml — one place per repo for
// "how is this thing built", whichever half of the stack you mean.
//
// Django SERVES the result: `frontend/dist/index.html` is returned for "/" and
// for each public route (see core/views/frontend.py), and WhiteNoise serves
// everything beside it from the origin root via settings.WHITENOISE_ROOT. That
// is why `base` stays "/" — the sources reference "/images/…" and
// "/videos/…" as absolute paths, so any other base would need every one of
// them rewritten.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  root: 'frontend',
  publicDir: 'public',
  build: {
    // Relative to `root`, i.e. frontend/dist. Emptied on every build so a
    // renamed chunk cannot linger and be served after it stopped being
    // referenced.
    outDir: 'dist',
    emptyOutDir: true,
    // Vite content-hashes these, which is what lets them be cached hard.
    assetsDir: 'assets',
  },
  // Vitest. jsdom because these are component tests — they render the real
  // components and assert on what a visitor would see, rather than snapshotting
  // markup. `setupFiles` installs jest-dom's matchers and the cleanup between
  // tests.
  //
  // Paths are relative to `root` (frontend/), like everything else above.
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.js',
    include: ['src/**/*.{test,spec}.{js,jsx}'],
    css: true,
    restoreMocks: true,
  },
  server: {
    port: 6001,
    // `npm run dev` gives HMR for the React site while proxying everything
    // Django owns to the container, so the two halves are reachable from ONE
    // origin in development exactly as they are in production. Without this,
    // the dev server answers /app/ with index.html and the management app
    // looks like a blank React page.
    proxy: {
      '/app': 'http://localhost:8000',
      '/static': 'http://localhost:8000',
      '/media': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
      '/api': 'http://localhost:8000',
    },
  },
})
