import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  // The sources moved under frontend/, so the build output is frontend/dist,
  // not 'dist'. Left unchanged this linted the 300 kB bundle and reported
  // ~8,400 problems in minified vendor code, which buries every real one.
  //
  // The rest of the repo is excluded by the npm script naming its targets
  // rather than by a growing ignore list here: `eslint .` in a repo that also
  // holds a Python virtualenv walks straight into Django's bundled jQuery.
  globalIgnores(['frontend/dist']),
  {
    // Scoped to the React sources so the config cannot wander into the Django
    // tree if a .js file ever lands there (core/static/js/ is linted by nothing
    // here — it is plain browser script, not part of this build).
    files: ['frontend/src/**/*.{js,jsx}', 'vite.config.js', 'eslint.config.js'],
    extends: [
      js.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
      parserOptions: {
        ecmaVersion: 'latest',
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    rules: {
      'no-unused-vars': ['error', { varsIgnorePattern: '^[A-Z_]' }],
    },
  },
])
