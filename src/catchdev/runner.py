"""Run the affected tests, and tell NEW failures from pre-existing ones.

Failures found in the working tree are re-run against the pre-diff baseline
in a temporary git worktree (checked out at the base ref). A failure that
also occurs there was already broken; only NEW failures are reported as
problems.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field

from .mapper import AffectedTests


@dataclass
class TestResult:
    test_id: str           # "tests/test_auth.py::test_token_refresh"
    passed: bool
    duration: float = 0.0
    error_message: str | None = None
    pre_existing: bool = False


@dataclass
class RunResult:
    results: list[TestResult] = field(default_factory=list)
    total_passed: int = 0
    total_failed: int = 0
    duration: float = 0.0
    new_failures: list[TestResult] = field(default_factory=list)
    pre_existing_failures: list[TestResult] = field(default_factory=list)
    no_tests: bool = False
    error: str | None = None


_RESULT_RE = re.compile(
    r"^(\S+?)\s+(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\s*(?:\[\s*\d+%\])?$"
)
_SUMMARY_RE = re.compile(r"^(FAILED|ERROR)\s+(\S+?)\s+-\s+(.*)$")
_SUMMARY_BARE_RE = re.compile(r"^(FAILED|ERROR)\s+(\S+?)$")
_SECTION_RE = re.compile(r"^_{3,}\s+(.+?)\s+_{3,}$")
_DURATION_RE = re.compile(r"^([\d.]+)s\s+(call|setup|teardown)\s+(\S+)$")

_PASS_STATUSES = {"PASSED", "XFAIL", "SKIPPED"}


def _run_pytest(targets: list[str], cwd: str,
                extra_env: dict[str, str] | None = None,
                timeout: int = 300) -> tuple[dict[str, str], dict[str, str],
                                             dict[str, float], int, str]:
    """Run pytest, return (statuses, error_messages, durations, rc, raw)."""
    cmd = [
        sys.executable, "-m", "pytest", *targets,
        "-v", "--tb=short", "--no-header", "--color=no",
        "-p", "no:cacheprovider", "--durations=0",
        "--continue-on-collection-errors",
    ]
    env = dict(os.environ)
    env.pop("PYTEST_ADDOPTS", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["COLUMNS"] = "240"  # stop pytest truncating summary lines to tty width
    if extra_env:
        env.update(extra_env)
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True,
            timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        return {}, {}, {}, -1, "pytest timed out"
    except FileNotFoundError:
        return {}, {}, {}, -1, "pytest is not installed"

    statuses: dict[str, str] = {}
    errors: dict[str, str] = {}
    durations: dict[str, float] = {}
    in_summary = False
    section: str | None = None  # current FAILURES section test
    for line in proc.stdout.splitlines():
        line = line.rstrip()
        if line.startswith("=") and "short test summary" in line:
            in_summary = True
            continue
        if in_summary:
            m = _SUMMARY_RE.match(line)
            if m:
                if m.group(2) not in errors:
                    errors[m.group(2)] = m.group(3).strip()[:500]
                statuses.setdefault(m.group(2), m.group(1))
                continue
            m = _SUMMARY_BARE_RE.match(line)
            if m:
                statuses.setdefault(m.group(2), m.group(1))
            continue
        m = _SECTION_RE.match(line)
        if m:
            section = m.group(1)
            if section.startswith("ERROR collecting "):
                # key is a file path, not a dotted test name
                section = section[len("ERROR collecting "):]
            else:
                section = section.replace(".", "::")
            continue
        if section and line.startswith("E "):
            errors[section] = line[2:].strip()[:500]
            section = None
            continue
        m = _RESULT_RE.match(line.strip())
        if m and "::" in m.group(1):
            statuses[m.group(1)] = m.group(2)
            continue
        m = _DURATION_RE.match(line.strip())
        if m:
            durations[m.group(3)] = durations.get(m.group(3), 0.0) + float(m.group(1))
    return statuses, errors, durations, proc.returncode, proc.stdout + proc.stderr


def _collect_targets(targets: list[str], cwd: str,
                     extra_env: dict[str, str] | None = None
                     ) -> tuple[dict[str, str], dict[str, str], dict[str, float], int, str]:
    """Run pytest over targets; if a fatal collection error (e.g. a broken
    conftest) kills the whole batch, fall back to per-target runs so one
    bad file can't hide the rest."""
    statuses, errors, durations, rc, raw = _run_pytest(targets, cwd, extra_env)
    if statuses or rc in (0, 5, -1) or len(targets) <= 1:
        return statuses, errors, durations, rc, raw
    for t in targets:
        s, e, d, rc2, raw2 = _run_pytest([t], cwd, extra_env)
        statuses.update(s)
        errors.update(e)
        for k, v in d.items():
            durations[k] = durations.get(k, 0.0) + v
        if not s and rc2 not in (0, 5):
            statuses[t] = "ERROR"
            errors.setdefault(t, _tail(raw2, 3))
    return statuses, errors, durations, 0 if statuses else rc, raw


def _git_ok(args: list[str], cwd: str) -> bool:
    return subprocess.run(
        ["git", *args], cwd=cwd,
        capture_output=True, text=True,
    ).returncode == 0


def _baseline_failures(failed_ids: list[str], cwd: str, base_ref: str) -> set[str] | None:
    """Re-run failed tests against the pre-diff code in a temp worktree.

    Returns the set of test ids that ALSO fail at baseline (= pre-existing),
    or None if a baseline run wasn't possible.
    """
    if not _git_ok(["rev-parse", "--verify", base_ref], cwd):
        return None
    tmp = tempfile.mkdtemp(prefix="catch-baseline-")
    try:
        if not _git_ok(["worktree", "add", "--detach", tmp, base_ref], cwd):
            return None
        # make the baseline importable: flat layout lives at the root,
        # src-layout lives in <root>/src — put both ahead of any
        # editable install of the *modified* code.
        paths = [tmp]
        if os.path.isdir(os.path.join(tmp, "src")):
            paths.insert(0, os.path.join(tmp, "src"))
        paths.append(os.environ.get("PYTHONPATH", ""))
        env_path = os.pathsep.join(p for p in paths if p)
        statuses, _, _, rc, raw = _collect_targets(
            failed_ids, cwd=tmp, extra_env={"PYTHONPATH": env_path}
        )
        if rc == -1 or not statuses:
            return None
        return {tid for tid, st in statuses.items() if st not in _PASS_STATUSES}
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", tmp],
                       cwd=cwd, capture_output=True)
        subprocess.run(["git", "worktree", "prune"],
                       cwd=cwd, capture_output=True)


def run_tests(affected: AffectedTests, all_tests: bool = False,
              cwd: str = ".", base_ref: str = "HEAD") -> RunResult:
    if all_tests:
        targets = ["."]
    else:
        # run pinpointed test functions, PLUS whole files where we only
        # have file-level confidence
        func_files = {t.split("::")[0] for t in affected.test_functions}
        targets = list(affected.test_functions) + [
            f for f in affected.test_files if f not in func_files
        ]

    result = RunResult()
    if not targets:
        result.no_tests = True
        return result

    start = time.monotonic()
    statuses, errors, durations, rc, raw = _collect_targets(targets, cwd)
    result.duration = time.monotonic() - start

    if rc == -1:
        result.error = raw.strip()
        return result
    if rc == 5 or (not statuses and rc != 0):
        # pytest collected nothing / failed at collection
        if not statuses:
            result.no_tests = rc == 5
            if rc != 0 and rc != 5:
                result.error = _tail(raw)
            return result

    for tid, status in sorted(statuses.items()):
        passed = status in _PASS_STATUSES
        result.results.append(TestResult(
            test_id=tid,
            passed=passed,
            duration=durations.get(tid, 0.0),
            error_message=None if passed else _error_for(tid, status, errors),
        ))

    failures = [r for r in result.results if not r.passed]
    if failures:
        baseline = _baseline_failures([f.test_id for f in failures], cwd, base_ref)
        for f in failures:
            if baseline is not None and _in_baseline(f.test_id, baseline):
                f.pre_existing = True
                result.pre_existing_failures.append(f)
            else:
                result.new_failures.append(f)

    result.total_passed = sum(1 for r in result.results if r.passed)
    result.total_failed = sum(1 for r in result.results if not r.passed)
    return result


def _in_baseline(test_id: str, baseline: set[str]) -> bool:
    """Did this test also fail at baseline?

    Handles id shape mismatches: a collection error is keyed by file path
    ('tests/test_x.py') while a collected run keys by node id
    ('tests/test_x.py::test_y').
    """
    if test_id in baseline:
        return True
    return any(
        b.startswith(test_id + "::") or test_id.startswith(b + "::")
        for b in baseline
    )


def _error_for(test_id: str, status: str, errors: dict[str, str]) -> str:
    """Best error message for a failed test id.

    FAILURES-section entries are keyed by 'Class::test[x]' (no file part),
    summary entries by full nodeid — try both.
    """
    if test_id in errors:
        return errors[test_id]
    for key, msg in errors.items():
        if test_id.endswith(key):
            return msg
    return status


def _tail(text: str, lines: int = 15) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])
