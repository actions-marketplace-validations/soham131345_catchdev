# catchdev

**Tells you what your AI coding agent just broke.**

![catch flags a real 4-month Flask regression in 2 seconds](demo/demo.gif)

That GIF is real: it's catch flagging the `SECRET_KEY_FALLBACKS` session bug
that lived in Flask 3.1.0 for ~4 months before anyone noticed. catch ran the
affected tests, re-ran the failures against the pre-diff baseline, and pointed
at the exact regression. [Reproduce it yourself.](demo/README.md)

## 10-second install

```bash
pip install catchdev
```

## Usage

```bash
cd some-ai-generated-project
# make some changes (or let an agent make them)
git add .
catch                 # check staged changes
catch --commit HEAD~1 # check the last commit
catch --all-tests     # run the full suite instead of affected tests
catch --skip-deps     # skip PyPI dependency risk checks
catch --fail-on-suspicious   # also block on suspicious (not just dangerous) deps
catch --output report.md     # write a markdown report (for PR comments)
echo $?               # 1 = problems found, 0 = clean
```

In a GitHub Actions PR, catch automatically diffs against
`origin/$GITHUB_BASE_REF` instead of `HEAD~1` (squash merges break `HEAD~1`).

## How it works

1. **differ** — parses `git diff --unified=0` into changed files, changed line
   numbers, and newly added imports / dependency entries.
2. **mapper** — builds (and caches in `.catch_cache.json`) an AST import graph
   of the project, then walks it backwards from each changed file until it hits
   test files. When possible it narrows down to the specific test functions
   that call the functions your diff touched.
3. **runner** — runs the affected tests with pytest, then re-runs any failures
   against the pre-diff baseline in a temporary git worktree to distinguish
   NEW failures from pre-existing ones.
4. **depcheck** — queries PyPI for every new dependency: does it exist, how old
   is it, how many downloads, and did you mean a similarly-named popular
   package? (Hallucinated package names — "slopsquatting" — are a real supply
   chain risk with AI-generated code.)
5. **report** — pretty terminal output and an exit code you can wire into a
   hook or CI.

## Why not just...?

| | catch | LLM review (CodeRabbit etc.) | static gates (dont-break, Solumbe) |
|---|---|---|---|
| Actually runs your tests | ✓ | ✗ | ✗ |
| Tells new failures from pre-existing | ✓ | ✗ | ✗ |
| Deterministic (same answer every run) | ✓ | ✗ | ✓ |
| Catches hallucinated packages | ✓ (live registry check) | unreliable | partial |
| Cost per run | free | tokens/subscription | free |
| Works as a hard CI gate | ✓ exit code | ✗ | ✓ |

## Use it everywhere

**GitHub Action** (posts the report as a PR comment):

```yaml
# .github/workflows/catch.yml
name: Catch
on: [pull_request]
jobs:
  catch:
    runs-on: ubuntu-latest
    permissions:
      issues: write
      pull-requests: write
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e .
      - uses: yourusername/catchdev@v1
```

catch runs catch on its own PRs — see
[.github/workflows/catch.yml](.github/workflows/catch.yml) in this repo.

**Pre-commit / pre-push hook** — add to `.pre-commit-config.yaml`:

```yaml
- repo: https://github.com/yourusername/catchdev
  rev: v0.1.0
  hooks:
    - id: catch
```

Running tests on every commit annoys people; pre-push is the sweet spot:

```yaml
    - id: catch
      stages: [pre-push]
```

**Claude Code skill** — your agent checks its own work before committing.
Copy [skills/catch/SKILL.md](skills/catch/SKILL.md) into
`~/.claude/skills/catch/` (or drop the packaged `catch.skill` from Releases
into your skills directory). After that, Claude runs `catch --staged` before
every commit and fixes what it breaks instead of shipping it.

## Exit codes

- `0` — all affected tests pass, no dangerous dependencies
- `1` — new test failures OR dangerous dependencies
- `2` — catch itself couldn't run (not a git repo, etc.)

## License

MIT
