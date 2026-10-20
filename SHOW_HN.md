# Launch copy — catchdev

## Show HN post

**Title:**
Show HN: Catch – runs the tests your AI coding agent just broke, flags hallucinated deps

**URL:** https://github.com/yourusername/catchdev

**First comment** (paste within 60 seconds of posting):

> The problem: 66% of developers say AI code is "almost right, but not quite"
> (Stack Overflow 2025 survey), and research found roughly half of AI-generated
> patches that pass benchmarks would be rejected by real maintainers. The
> missing piece: existing AI review tools *read* code; almost nothing *runs*
> your own tests against the diff. Catch maps the diff to affected tests via an
> import graph, runs only those, re-runs failures against the pre-diff baseline
> to filter out pre-existing breakage, and checks new deps against PyPI to
> catch hallucinated package names (slopsquatting). Exit code 0/1, so it works
> as a pre-push hook, CI gate, or a Claude Code skill so agents check their own
> work. `pip install catchdev`. Happy to answer anything — the trickiest parts
> were the baseline worktree trick and making test-selection precise enough to
> be fast on real codebases.

**Proof point to mention if asked "does it work on real code":** the README GIF
is catch flagging the SECRET_KEY_FALLBACKS regression that lived in Flask
3.1.0 for ~4 months (reproduction script in demo/). We run catch on catchdev's
own PRs.

## Reddit (r/ClaudeAI) headline

> Your agent can now catch its own bugs — a Claude Code skill that runs your
> affected tests before every commit (open source)

## Launch-day checklist

- [ ] PyPI: `twine upload dist/*` (name `catchdev` confirmed AVAILABLE as of build day)
- [ ] GitHub repo public, topics set, release v0.1.0 with Marketplace checkbox
      (categories: Code quality, Testing)
- [ ] Screenshot the Action running on a catchdev PR (second demo)
- [ ] Post Show HN (Tue–Thu, ~13:00–15:00 UTC), paste first comment immediately
- [ ] r/ClaudeAI post with the GIF
