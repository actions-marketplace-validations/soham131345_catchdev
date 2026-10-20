"""Pretty terminal output for catch, built on rich."""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel

from .depcheck import DepRisk
from .differ import DiffResult
from .mapper import AffectedTests
from .runner import RunResult

console = Console()
err_console = Console(stderr=True)


def _file_of(test_id: str) -> str:
    return test_id.split("::")[0]


def print_report(diff: DiffResult, affected: AffectedTests,
                 results: RunResult, dep_risks: list[DepRisk],
                 fail_on_suspicious: bool = False) -> None:
    n_files = len(diff.files)
    n_tests = len(results.results) or len(affected.test_files)
    console.print(Panel(
        f"[bold]{n_files} file{'s' if n_files != 1 else ''} changed, "
        f"{n_tests} test{'s' if n_tests != 1 else ''} affected[/bold]",
        title="CATCH REPORT",
        expand=False,
    ))

    if results.error:
        err_console.print(f"[red]✗ could not run tests:[/red] {results.error}")

    # --- new failures -----------------------------------------------------
    if results.new_failures:
        console.print(f"\n[bold red]✗ NEW FAILURES "
                      f"({len(results.new_failures)})[/bold red]")
        seen_reasons: set[str] = set()
        for f in results.new_failures:
            console.print(f"  [red]{f.test_id}[/red]")
            reasons = affected.reasons.get(_file_of(f.test_id), [])
            for r in reasons:
                if (f.test_id, r) not in seen_reasons:
                    console.print(f"    [dim]→ {r}[/dim]")
                    seen_reasons.add((f.test_id, r))
            if f.error_message:
                console.print(f"    [dim]→ {f.error_message}[/dim]")

    # --- pre-existing failures --------------------------------------------
    if results.pre_existing_failures:
        console.print(f"\n[dim]~ PRE-EXISTING FAILURES "
                      f"({len(results.pre_existing_failures)}) — "
                      f"already broken before your diff[/dim]")
        for f in results.pre_existing_failures:
            console.print(f"  [dim]{f.test_id}[/dim]")

    # --- dependency risks ---------------------------------------------------
    risky = [d for d in dep_risks if d.risk_level != "safe"]
    if risky:
        console.print(f"\n[bold yellow]⚠ SUSPICIOUS DEPENDENCIES "
                      f"({len(risky)})[/bold yellow]")
        for d in risky:
            color = "red" if d.risk_level == "dangerous" else "yellow"
            console.print(f"  [{color}]{d.package}[/{color}] "
                          f"[dim]({d.risk_level})[/dim]")
            console.print(f"    [dim]→ {d.reason}[/dim]")
            if d.similar_packages:
                console.print(
                    f"    [dim]→ did you mean: "
                    f"{', '.join(d.similar_packages)}?[/dim]"
                )

    # --- summary ------------------------------------------------------------
    if results.no_tests:
        console.print("\n[yellow]⚠ no tests found — can't verify anything, "
                      "commit at your own risk[/yellow]")
    elif results.total_passed:
        console.print(f"\n[green]✓ {results.total_passed} affected "
                      f"test{'s' if results.total_passed != 1 else ''} "
                      f"pass[/green]")

    code = get_exit_code(results, dep_risks, fail_on_suspicious)
    if results.no_tests:
        console.print("\n[bold yellow]RESULT: UNKNOWN[/bold yellow] — "
                      "no tests to verify against")
    elif code == 0:
        console.print("\n[bold green]RESULT: PASS[/bold green] — safe to commit")
    else:
        console.print("\n[bold red]RESULT: FAIL[/bold red] — do not commit")


def get_exit_code(results: RunResult, dep_risks: list[DepRisk],
                  fail_on_suspicious: bool = False) -> int:
    """0 = clean. 1 = new failures or dangerous deps. 2 = catch couldn't run."""
    if results.error:
        return 2
    if results.new_failures:
        return 1
    blocking = {"dangerous"} | ({"suspicious"} if fail_on_suspicious else set())
    if any(d.risk_level in blocking for d in dep_risks):
        return 1
    return 0


def render_markdown(diff: DiffResult, affected: AffectedTests,
                    results: RunResult, dep_risks: list[DepRisk],
                    fail_on_suspicious: bool = False) -> str:
    """Markdown version of the report (for PR comments via --output)."""
    n_files = len(diff.files)
    n_tests = len(results.results) or len(affected.test_files)
    lines = [
        "## Catch report",
        "",
        f"**{n_files} file{'s' if n_files != 1 else ''} changed, "
        f"{n_tests} test{'s' if n_tests != 1 else ''} affected**",
        "",
    ]
    if results.error:
        lines += [f"❌ could not run tests: `{results.error}`", ""]
    if results.new_failures:
        lines += [f"### ❌ New failures ({len(results.new_failures)})", ""]
        for f in results.new_failures:
            lines.append(f"- `{f.test_id}`")
            for r in affected.reasons.get(_file_of(f.test_id), []):
                lines.append(f"  - {r}")
            if f.error_message:
                lines.append(f"  - `{f.error_message}`")
        lines.append("")
    if results.pre_existing_failures:
        lines += [f"<details><summary>~ Pre-existing failures "
                  f"({len(results.pre_existing_failures)}) — already broken "
                  f"before this diff</summary>", ""]
        for f in results.pre_existing_failures:
            lines.append(f"- `{f.test_id}`")
        lines += ["", "</details>", ""]
    risky = [d for d in dep_risks if d.risk_level != "safe"]
    if risky:
        lines += [f"### ⚠️ Suspicious dependencies ({len(risky)})", ""]
        for d in risky:
            lines.append(f"- **{d.package}** ({d.risk_level}) — {d.reason}")
            if d.similar_packages:
                lines.append(f"  - did you mean: "
                             f"{', '.join(f'`{p}`' for p in d.similar_packages)}?")
        lines.append("")
    if results.no_tests:
        lines += ["> ⚠️ No tests found — can't verify anything, "
                  "merge at your own risk.", ""]
    elif results.total_passed:
        lines += [f"✅ {results.total_passed} affected "
                  f"test{'s' if results.total_passed != 1 else ''} pass", ""]
    code = get_exit_code(results, dep_risks, fail_on_suspicious)
    if results.no_tests:
        lines.append("**RESULT: UNKNOWN** — no tests to verify against")
    elif code == 0:
        lines.append("**RESULT: PASS** ✅ — safe to merge")
    else:
        lines.append("**RESULT: FAIL** ❌ — do not merge")
    lines += ["", "---", "<sub>Posted by "
              "[catch](https://github.com/yourusername/catchdev) — "
              "tells you what your AI coding agent just broke</sub>"]
    return "\n".join(lines) + "\n"
