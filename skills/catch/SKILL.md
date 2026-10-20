---
name: catch
description: Verify changes before committing. Run `catch` after making code changes to detect which existing tests your edits break and flag suspicious new dependencies. Use before creating commits or pull requests.
---

Before committing or opening a PR:

1. Stage your changes: `git add -A`
2. Run `catch --staged`
3. If exit code is 1:
   - Read the NEW FAILURES section carefully
   - Fix the regression OR explain to the user why the behavior change is intentional
   - For SUSPICIOUS DEPENDENCIES: verify the package name is correct; if hallucinated, find the real package
4. Only commit when catch exits 0, or the user explicitly approves the failures

Never commit code that catch flags without telling the user.
