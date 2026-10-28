"""Parse git diffs into structured change data.

Input:  `git diff` (staged changes, or `git diff <commit>` for a commit range)
Output: DiffResult — which files changed, which lines, and which new
        third-party dependencies were introduced.
"""
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ChangedFile:
    path: str                    # "src/auth/token.py"
    changed_lines: list[int] = field(default_factory=list)  # [45, 46, 47, 89]
    is_new_file: bool = False
    is_deleted: bool = False
    is_python: bool = True


@dataclass
class DiffResult:
    files: list[ChangedFile] = field(default_factory=list)
    new_dependencies: list[str] = field(default_factory=list)  # ["py-token-utils"]


_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_HEADER_RE = re.compile(r"^diff --git a/(.*?) b/(.*)$")
_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([A-Za-z_][\w.]*)\s+import\s|import\s+([A-Za-z_][\w.]*))"
)
_REQ_LINE_RE = re.compile(r"^\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*(?:[<>=!~;\[ ]|$)")
_TOML_DEP_RE = re.compile(r"""["']([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)""")

# import name -> PyPI package name for common mismatches
_IMPORT_TO_PACKAGE = {
    "yaml": "pyyaml",
    "PIL": "pillow",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "bs4": "beautifulsoup4",
    "jwt": "PyJWT",
    "Crypto": "pycryptodome",
    "dateutil": "python-dateutil",
    "magic": "python-magic",
    "serial": "pyserial",
    "usb": "pyusb",
    "dns": "dnspython",
    "ldap": "python-ldap",
    "gi": "PyGObject",
}


class GitError(RuntimeError):
    """Raised when git can't give us a diff (not a repo, bad ref, ...)."""


def _git(args: list[str], cwd: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise GitError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


def get_diff(staged: bool = True, commit: str | None = None, cwd: str = ".") -> DiffResult:
    """Get the diff to analyse.

    staged=True (default): staged changes, falling back to unstaged if the
        index is empty.
    commit="HEAD~1": changes introduced since that commit.
    """
    if commit:
        text = _git(["diff", "--unified=0", commit], cwd)
    elif staged:
        text = _git(["diff", "--cached", "--unified=0"], cwd)
        if not text.strip():
            text = _git(["diff", "--unified=0"], cwd)
    else:
        text = _git(["diff", "--unified=0"], cwd)
    return parse_diff(text, cwd=cwd)


def parse_diff(text: str, cwd: str = ".") -> DiffResult:
    """Parse `git diff --unified=0` output into a DiffResult."""
    result = DiffResult()
    current: ChangedFile | None = None
    added_lines: list[str] = []
    old_path = ""

    def flush():
        nonlocal current, added_lines
        if current is None:
            return
        current.changed_lines = sorted(set(current.changed_lines))
        result.files.append(current)
        deps = _extract_deps(current, added_lines, cwd)
        for d in deps:
            if d not in result.new_dependencies:
                result.new_dependencies.append(d)
        current = None
        added_lines = []

    for line in text.splitlines():
        if line.startswith("diff --git "):
            flush()
            m = _HEADER_RE.match(line)
            # fallback path for binary files (no +++ line follows)
            current = ChangedFile(path=m.group(2) if m else "")
            current.is_python = current.path.endswith(".py")
            old_path = ""
        elif current is None:
            continue
        elif line.startswith("new file mode"):
            current.is_new_file = True
        elif line.startswith("deleted file mode"):
            current.is_deleted = True
        elif line.startswith("rename to "):
            current.path = line[len("rename to "):].strip()
        elif line.startswith("--- "):
            target = line[4:].strip()
            old_path = target[2:] if target.startswith("a/") else target
        elif line.startswith("+++ "):
            target = line[4:].strip()
            if target == "/dev/null":
                current.is_deleted = True
                current.path = old_path
                current.is_python = current.path.endswith(".py")
            else:
                current.path = target[2:] if target.startswith("b/") else target
                current.is_python = current.path.endswith(".py")
        elif line.startswith("@@ "):
            m = _HUNK_RE.match(line)
            if m:
                start = int(m.group(1))
                count = int(m.group(2)) if m.group(2) else 1
                if count > 0:
                    current.changed_lines.extend(range(start, start + count))
        elif line.startswith("+") and not line.startswith("+++"):
            added_lines.append(line[1:])

    flush()
    return result


def _extract_deps(cf: ChangedFile, added_lines: list[str], cwd: str) -> list[str]:
    """Pull new third-party dependency names out of added lines."""
    if cf.is_deleted:
        return []
    name = cf.path
    if name.endswith(".py"):
        return _extract_python_imports(added_lines, cwd)
    if name.endswith("requirements.txt") or "requirements" in Path(name).name:
        return _extract_requirements(added_lines)
    if name.endswith("pyproject.toml"):
        return _extract_pyproject_deps(added_lines)
    return []


def _extract_python_imports(lines: list[str], cwd: str) -> list[str]:
    local = _local_top_level_modules(cwd)
    deps: list[str] = []
    for line in lines:
        m = _IMPORT_RE.match(line)
        if not m:
            continue
        module = (m.group(1) or m.group(2)).split(".")[0]
        if module in _stdlib_names() or module in local:
            continue
        package = _IMPORT_TO_PACKAGE.get(module, module)
        if package not in deps:
            deps.append(package)
    return deps


def _extract_requirements(lines: list[str]) -> list[str]:
    deps: list[str] = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        m = _REQ_LINE_RE.match(line)
        if m and m.group(1) not in deps:
            deps.append(m.group(1))
    return deps


def _extract_pyproject_deps(lines: list[str]) -> list[str]:
    deps: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith(('"', "'")) and "=" in stripped:
            # a key = ... line (e.g. dependencies = ["a", "b"]) — still parse quotes
            pass
        for m in _TOML_DEP_RE.finditer(stripped):
            token = m.group(1)
            # skip version numbers and obvious non-packages
            if token[0].isdigit() or token in ("toml",):
                continue
            if token not in deps:
                deps.append(token)
    return deps


_STDLIB: frozenset[str] | None = None


def _stdlib_names() -> frozenset[str]:
    global _STDLIB
    if _STDLIB is None:
        _STDLIB = frozenset(sys.stdlib_module_names)
    return _STDLIB


def _local_top_level_modules(root: str) -> set[str]:
    """Top-level importable names that live in this project (not PyPI)."""
    mods: set[str] = set()
    root_path = Path(root)
    if not root_path.is_dir():
        return mods
    for child in root_path.iterdir():
        if child.name.startswith("."):
            continue
        if child.is_dir():
            if (child / "__init__.py").exists() or any(child.glob("*.py")):
                mods.add(child.name)
            if child.name in ("src", "lib"):  # src-layout
                for sub in child.iterdir():
                    if sub.is_dir() and (
                        (sub / "__init__.py").exists() or any(sub.glob("*.py"))
                    ):
                        mods.add(sub.name)
                    elif sub.suffix == ".py":
                        mods.add(sub.stem)
        elif child.suffix == ".py":
            mods.add(child.stem)
    return mods