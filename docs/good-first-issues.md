# Good first issues

File these as GitHub issues with the `good first issue` label after the repo
goes public. They double as the JS-expansion roadmap signal.

1. **Support unittest runner** — runner.py currently shells out to pytest.
   Add a `--runner unittest` path that discovers and runs affected
   `unittest.TestCase` methods and parses their results.

2. **npm dependency checking** — depcheck.py only queries PyPI. Add the same
   hallucination/age/download checks for `package.json` additions against the
   npm registry (`https://registry.npmjs.org/{pkg}`).

3. **Jest support** — mapper.py assumes Python AST. A first JS step: map
   changed `.js/.ts` files to `*.test.js` / `__tests__` files via static
   `import`/`require` parsing, run them with `jest --findRelatedTests`.

4. **`catch --init`** — interactive setup that writes a pre-commit config and
   the GitHub workflow into the current repo.

5. **Baseline caching** — the baseline worktree re-runs failures on every
   invocation. Cache baseline results keyed by (base ref, test id) in
   `.catch_cache.json` to make repeated runs near-instant.
