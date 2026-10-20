# The killer demo: catch vs. a real 4-month Flask bug

**The bug.** Flask 3.1.0 (2024-11-13) shipped `SECRET_KEY_FALLBACKS` key
rotation with the fallback keys in the wrong order — sessions signed with an
older key were silently dropped. It went unnoticed for ~4 months until
[`fb541598`](https://github.com/pallets/flask/commit/fb54159861708558b5f5658ebdc14709d984361c)
("secret key rotation: fix key list ordering", merged 2025-03-10, released in
3.1.1 on 2025-05-13) fixed it and added the regression test.

**The demo.** Check out Flask one commit before the fix, stage the regression
test that later exposed the bug, run `catch`. It maps the change through the
import graph, runs the affected tests, re-runs failures against the pre-diff
baseline — and flags exactly the regression, in about 2 seconds:

```
╭─────────── CATCH REPORT ────────────╮
│ 2 files changed, 350 tests affected │
╰─────────────────────────────────────╯

✗ NEW FAILURES (1)
  tests/test_basic.py::test_session_secret_key_fallbacks
    → diff touches src/flask/sessions.py:open_session() (via
      src/flask/globals.py → src/flask/debughelpers.py)
    → test file itself changed:test_session_secret_key_fallbacks()
    → AssertionError: assert {'a': 1} == {}

~ PRE-EXISTING FAILURES (3) — already broken before your diff
  examples/javascript/tests/test_js_example.py
  examples/tutorial/tests/test_auth.py
  tests/test_reqctx.py::test_bad_environ_raises_bad_request

✓ 346 affected tests pass

RESULT: FAIL — do not commit
```

(verbatim output; the two `examples/` errors and the `test_reqctx` failure are
environment artifacts of running a 2025 tree against 2026 dependency versions —
catch correctly classifies them as pre-existing by re-running them against the
baseline in a temp git worktree.)

## Reproduce it yourself

```bash
git clone https://github.com/pallets/flask.git && cd flask
git checkout fb541598~1          # the day before the fix; bug is live
git show fb541598:tests/test_basic.py > tests/test_basic.py
pip install -e . "pytest==8.3.5" # pytest 9 removed monkeypatch.notset (used by flask's conftest then)
git add -A
catch                            # → red ✗, exit code 1
```

Sanity check: `git checkout fb541598` (the fix) and the same test passes.

## About demo.gif

`demo.gif` re-renders this exact recorded run as an animated terminal
(readable font, < 15 s, < 1 MB). The command sequence, test id, assertion and
counts are verbatim from the real run above. To record a live version instead:

```bash
asciinema rec demo.cast        # run the steps above
# then convert, e.g. with agg (https://github.com/asciinema/agg):
agg demo.cast demo.gif
```

