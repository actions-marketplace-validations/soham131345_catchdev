"""Unit tests for catchdev internals."""
from __future__ import annotations

import textwrap

from catchdev import depcheck, differ, mapper, report
from catchdev.runner import RunResult, TestResult

SAMPLE_DIFF = textwrap.dedent("""\
    diff --git a/src/auth/token.py b/src/auth/token.py
    index 1111111..2222222 100644
    --- a/src/auth/token.py
    +++ b/src/auth/token.py
    @@ -45,0 +46,3 @@ def refresh(token):
    +import py_token_utils
    +from datetime import timedelta
    +import os
    @@ -89 +91 @@ def refresh(token):
    -old_line
    +new_line
    diff --git a/notes.md b/notes.md
    index 3333333..4444444 100644
    --- a/notes.md
    +++ b/notes.md
    @@ -1 +1 @@
    -old
    +new
    diff --git a/requirements.txt b/requirements.txt
    index 5555555..6666666 100644
    --- a/requirements.txt
    +++ b/requirements.txt
    @@ -2,0 +3 @@
    +evil-package-xyz==0.0.1
    diff --git a/src/new_mod.py b/src/new_mod.py
    new file mode 100644
    index 0000000..7777777
    --- /dev/null
    +++ b/src/new_mod.py
    @@ -0,0 +1,2 @@
    +import requests
    +x = 1
    diff --git a/src/old_mod.py b/src/old_mod.py
    deleted file mode 100644
    index 8888888..0000000
    --- a/src/old_mod.py
    +++ /dev/null
    @@ -1,2 +0,0 @@
    -import os
    -y = 2
""")


def test_parse_diff_files():
    result = differ.parse_diff(SAMPLE_DIFF, cwd="/nonexistent")
    by_path = {f.path: f for f in result.files}
    assert "src/auth/token.py" in by_path
    assert "notes.md" in by_path
    assert "src/new_mod.py" in by_path
    assert "src/old_mod.py" in by_path

    token = by_path["src/auth/token.py"]
    assert token.changed_lines == [46, 47, 48, 91]
    assert token.is_python and not token.is_new_file and not token.is_deleted

    assert not by_path["notes.md"].is_python
    assert by_path["src/new_mod.py"].is_new_file
    assert by_path["src/new_mod.py"].changed_lines == [1, 2]
    assert by_path["src/old_mod.py"].is_deleted
    assert by_path["src/old_mod.py"].changed_lines == []


def test_parse_diff_new_dependencies():
    result = differ.parse_diff(SAMPLE_DIFF, cwd="/nonexistent")
    # stdlib (os, datetime) excluded; third-party + requirements.txt included
    assert "py_token_utils" in result.new_dependencies
    assert "evil-package-xyz" in result.new_dependencies
    assert "requests" in result.new_dependencies
    assert "os" not in result.new_dependencies
    assert "datetime" not in result.new_dependencies


def test_depcheck_classify():
    assert depcheck._classify(False, None, None)[0] == "dangerous"
    assert depcheck._classify(None, None, None)[0] == "suspicious"
    assert depcheck._classify(True, 4, 12)[0] == "dangerous"
    assert depcheck._classify(True, 60, 500)[0] == "suspicious"
    assert depcheck._classify(True, 400, 5000000)[0] == "safe"
    assert depcheck._classify(True, 10, 5000000)[0] == "safe"  # popular beats young


def test_depcheck_similar():
    assert "requests" in depcheck._similar("reqeusts")
    assert "pyjwt" in depcheck._similar("pyjw")


def test_mapper_end_to_end(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "core.py").write_text(
        "def helper():\n    return 1\n\n\ndef other():\n    return 2\n"
    )
    (tmp_path / "pkg" / "api.py").write_text(
        "from pkg.core import helper\n\n\ndef handle():\n    return helper()\n"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_core.py").write_text(
        "from pkg.core import helper\n\n\ndef test_helper():\n    assert helper() == 1\n"
    )
    (tmp_path / "tests" / "test_api.py").write_text(
        "from pkg.api import handle\n\n\ndef test_handle():\n    assert handle() == 1\n"
    )
    diff = differ.DiffResult(files=[differ.ChangedFile(
        path="pkg/core.py", changed_lines=[1, 2],
    )])
    affected = mapper.find_affected_tests(diff, root=str(tmp_path))
    assert set(affected.test_files) == {"tests/test_api.py", "tests/test_core.py"}
    # function-level precision: both tests call into helper()
    assert "tests/test_core.py::test_helper" in affected.test_functions
    assert affected.confidence in ("high", "medium")
    assert affected.reasons["tests/test_core.py"]


def test_mapper_test_file_changed_directly(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_x():\n    assert True\n")
    diff = differ.DiffResult(files=[differ.ChangedFile(
        path="tests/test_x.py", changed_lines=[1],
    )])
    affected = mapper.find_affected_tests(diff, root=str(tmp_path))
    assert affected.test_files == ["tests/test_x.py"]


def test_mapper_cache_written(tmp_path):
    (tmp_path / "a.py").write_text("import b\n")
    (tmp_path / "b.py").write_text("x = 1\n")
    mapper.build_import_graph(str(tmp_path))
    assert (tmp_path / ".catch_cache.json").exists()


def test_exit_code_logic():
    ok = RunResult(results=[TestResult("t::a", True)], total_passed=1)
    bad = RunResult(new_failures=[TestResult("t::a", False)], total_failed=1)
    assert report.get_exit_code(ok, []) == 0
    assert report.get_exit_code(bad, []) == 1
    danger = depcheck.DepRisk(package="x", exists=False, risk_level="dangerous")
    susp = depcheck.DepRisk(package="y", exists=True, risk_level="suspicious")
    assert report.get_exit_code(ok, [danger]) == 1
    assert report.get_exit_code(ok, [susp]) == 0
    # --fail-on-suspicious upgrades suspicious to blocking
    assert report.get_exit_code(ok, [susp], fail_on_suspicious=True) == 1
    assert report.get_exit_code(RunResult(error="boom"), []) == 2


def test_render_markdown():
    diff = differ.DiffResult(files=[differ.ChangedFile(path="a.py")])
    affected = mapper.AffectedTests(
        test_files=["tests/test_a.py"],
        test_functions=["tests/test_a.py::test_x"],
        confidence="high",
        reasons={"tests/test_a.py": ["diff touches a.py:foo()"]},
    )
    results = RunResult(
        results=[TestResult("tests/test_a.py::test_x", False,
                            error_message="AssertionError: nope")],
        total_failed=1,
        new_failures=[TestResult("tests/test_a.py::test_x", False,
                                 error_message="AssertionError: nope")],
    )
    md = report.render_markdown(diff, affected, results, [])
    assert md.startswith("## Catch report")
    assert "tests/test_a.py::test_x" in md
    assert "AssertionError: nope" in md
    assert "**RESULT: FAIL**" in md
    ok = RunResult(results=[TestResult("t::a", True)], total_passed=1)
    md_ok = report.render_markdown(diff, affected, ok, [])
    assert "**RESULT: PASS**" in md_ok
