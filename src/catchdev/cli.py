"""catch — Tells you what your AI coding agent just broke."""
from __future__ import annotations

import os
import sys

import click

from . import depcheck, differ, mapper, report, runner


@click.command()
@click.option("--staged", is_flag=True, default=False,
              help="Check staged changes (default)")
@click.option("--commit", default=None,
              help="Check changes since a specific commit (e.g., HEAD~1)")
@click.option("--all-tests", is_flag=True, default=False,
              help="Run all tests, not just affected ones")
@click.option("--skip-deps", is_flag=True, default=False,
              help="Skip dependency checking")
@click.option("--fail-on-suspicious", is_flag=True, default=False,
              help="Exit 1 on suspicious (not just dangerous) dependencies")
@click.option("--output", default=None, metavar="FILE",
              help="Also write the report as markdown to FILE "
                   "(e.g. report.md for PR comments)")
def main(staged: bool, commit: str | None, all_tests: bool, skip_deps: bool,
         fail_on_suspicious: bool, output: str | None) -> None:
    """Tells you what your AI coding agent just broke."""
    # In CI pull requests, diff against the PR base, not HEAD~1
    # (squash merges break HEAD~1). GITHUB_BASE_REF is set by GitHub Actions.
    base_ref_env = os.environ.get("GITHUB_BASE_REF")
    if commit is None and base_ref_env and not staged:
        commit = f"origin/{base_ref_env}"

    # 1. Get the diff
    try:
        diff = differ.get_diff(staged=staged or not commit, commit=commit)
    except differ.GitError as e:
        report.err_console.print(f"[red]✗ {e}[/red]")
        sys.exit(2)

    if not diff.files:
        report.console.print("[green]✓ no changes detected[/green]")
        sys.exit(0)

    # 2. Map to affected tests
    affected = mapper.find_affected_tests(diff)

    # 3. Run tests (baseline = the ref we diffed against)
    results = runner.run_tests(
        affected, all_tests=all_tests, base_ref=commit or "HEAD"
    )

    # 4. Check dependencies
    dep_risks = [] if skip_deps else depcheck.check_dependencies(
        diff.new_dependencies
    )

    # 5. Report (terminal + optional markdown file)
    report.print_report(diff, affected, results, dep_risks,
                        fail_on_suspicious=fail_on_suspicious)
    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(report.render_markdown(
                diff, affected, results, dep_risks,
                fail_on_suspicious=fail_on_suspicious,
            ))

    # 6. Exit code
    sys.exit(report.get_exit_code(results, dep_risks,
                                  fail_on_suspicious=fail_on_suspicious))


if __name__ == "__main__":
    main()
