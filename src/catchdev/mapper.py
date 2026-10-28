"""Map changed source files to the tests they can break.

Builds an AST import graph of the whole project (cached in
.catch_cache.json — only files whose mtime/size changed are re-parsed),
then walks the *reverse* graph from each changed file until it reaches
test files. When possible it narrows the result to the specific test
functions that reference the functions your diff touched.
"""
from __future__ import annotations

import ast
import fnmatch
import json
from dataclasses import dataclass, field
from pathlib import Path

from .differ import DiffResult

CACHE_NAME = ".catch_cache.json"
_IGNORE_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".venv", "venv", "env",
    "node_modules", ".mypy_cache", ".pytest_cache", ".tox", ".idea",
    "dist", "build", ".eggs",
}
_CACHE_VERSION = 4


@dataclass
class AffectedTests:
    test_files: list[str] = field(default_factory=list)      # ["tests/test_auth.py"]
    test_functions: list[str] = field(default_factory=list)  # ["tests/test_auth.py::test_x"]
    confidence: str = "low"                                  # "high" | "medium" | "low"
    # test_file -> human-readable reasons it is affected
    reasons: dict[str, list[str]] = field(default_factory=dict)


def is_test_file(path: str) -> bool:
    name = Path(path).name
    if fnmatch.fnmatch(name, "test_*.py") or fnmatch.fnmatch(name, "*_test.py"):
        return True
    return Path(path).parent.name in ("tests", "test") and name.endswith(".py")


# ---------------------------------------------------------------------------
# import graph (with caching)
# ---------------------------------------------------------------------------

def _discover_python_files(root: Path) -> list[str]:
    out: list[str] = []
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root)
        if any(part in _IGNORE_DIRS or part.startswith(".") for part in rel.parts):
            continue
        out.append(rel.as_posix())
    return out


def _module_names(rel_path: str) -> list[str]:
    """Candidate dotted module names for a file (handles src-layout)."""
    parts = rel_path[:-3].split("/")  # strip .py
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return []
    names = [".".join(parts)]
    if parts[0] in ("src", "lib") and len(parts) > 1:
        names.append(".".join(parts[1:]))
    return names


def _raw_imports(path: Path, rel_path: str) -> list[str]:
    """Dotted module names this file imports (unresolved), including
    relative imports resolved against the file's own package path."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError):
        return []
    # package context for resolving `from . import x` / `from .mod import x`
    parts = rel_path[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
        pkg = parts
    else:
        pkg = parts[:-1]
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                base = node.module
            elif node.level > 0 and node.level <= len(pkg):
                anchor = ".".join(pkg[: len(pkg) - node.level + 1])
                base = f"{anchor}.{node.module}" if node.module else anchor
            else:
                continue
            if not base:
                continue
            mods.append(base)
            for a in node.names:  # `from pkg import mod` may import a module
                mods.append(f"{base}.{a.name}")
    return mods


def build_import_graph(root: str = ".") -> dict[str, set[str]]:
    """Return {file: {internal files it imports}}, using the disk cache."""
    root_path = Path(root).resolve()
    cache_file = root_path / CACHE_NAME
    cache: dict = {"version": _CACHE_VERSION, "entries": {}}
    if cache_file.exists():
        try:
            loaded = json.loads(cache_file.read_text())
            if loaded.get("version") == _CACHE_VERSION:
                cache = loaded
        except (json.JSONDecodeError, OSError):
            pass

    files = _discover_python_files(root_path)
    entries: dict[str, dict] = cache.get("entries", {})
    new_entries: dict[str, dict] = {}

    # module index for resolution
    index: dict[str, str] = {}
    for rel in files:
        for mod in _module_names(rel):
            index.setdefault(mod, rel)

    file_set = set(files)
    for rel in files:
        p = root_path / rel
        try:
            stat = p.stat()
        except OSError:
            continue
        old = entries.get(rel)
        if old and old.get("mtime") == stat.st_mtime and old.get("size") == stat.st_size:
            new_entries[rel] = old
        else:
            new_entries[rel] = {
                "mtime": stat.st_mtime,
                "size": stat.st_size,
                "imports": _raw_imports(p, rel),
            }

    graph: dict[str, set[str]] = {}
    for rel, entry in new_entries.items():
        resolved: set[str] = set()
        for mod in entry["imports"]:
            target = index.get(mod)
            if target and target != rel and target in file_set:
                resolved.add(target)
        graph[rel] = resolved

    try:
        cache_file.write_text(json.dumps(
            {"version": _CACHE_VERSION, "entries": new_entries}
        ))
    except OSError:
        pass
    return graph


# ---------------------------------------------------------------------------
# function-level precision
# ---------------------------------------------------------------------------

def _changed_function_names(path: Path, changed_lines: list[int]) -> list[str]:
    """Names of functions whose body intersects the changed line numbers."""
    if not changed_lines or not path.exists():
        return []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError):
        return []
    changed = set(changed_lines)
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = node.end_lineno or node.lineno
            if any(node.lineno <= ln <= end for ln in changed):
                names.append(node.name)
    return names


def _test_functions_referencing(path: Path, names: set[str]) -> list[str]:
    """Test functions in a file that reference any of the given names."""
    if not names or not path.exists():
        return []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError):
        return []
    hits: list[str] = []
    for node in tree.body if hasattr(tree, "body") else []:
        targets = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            targets = [(node, "")]
        elif isinstance(node, ast.ClassDef):  # test classes
            targets = [(n, f"{node.name}::") for n in node.body
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for fn, prefix in targets:
            if not fn.name.startswith("test"):
                continue
            for sub in ast.walk(fn):
                if isinstance(sub, ast.Name) and sub.id in names:
                    hits.append(prefix + fn.name)
                    break
                if isinstance(sub, ast.Attribute) and sub.attr in names:
                    hits.append(prefix + fn.name)
                    break
    return hits


# ---------------------------------------------------------------------------
# main entry
# ---------------------------------------------------------------------------

def find_affected_tests(diff: DiffResult, root: str = ".") -> AffectedTests:
    root_path = Path(root).resolve()
    result = AffectedTests()

    py_changes = [f for f in diff.files if f.is_python and not f.is_deleted]
    deleted = [f for f in diff.files if f.is_python and f.is_deleted]
    if not py_changes and not deleted:
        return result

    graph = build_import_graph(root)
    reverse: dict[str, set[str]] = {}
    for src, targets in graph.items():
        for t in targets:
            reverse.setdefault(t, set()).add(src)

    affected: dict[str, list[str]] = {}  # test file -> reasons
    symbol_sources: dict[str, set[str]] = {}  # test file -> changed symbols
    changed_test_funcs: list[str] = []  # test functions that were edited directly

    def note(test_file: str, reason: str, symbols: list[str]):
        affected.setdefault(test_file, [])
        if reason not in affected[test_file]:
            affected[test_file].append(reason)
        symbol_sources.setdefault(test_file, set()).update(symbols)

    # BFS on the reverse import graph from every changed file
    for cf in py_changes:
        path = cf.path
        symbols = _changed_function_names(root_path / path, cf.changed_lines)
        sym_desc = f":{', '.join(symbols)}()" if symbols else ""
        if is_test_file(path):
            note(path, f"test file itself changed{sym_desc}", symbols)
            # run the edited test functions directly, not the whole file
            for fn in symbols:
                if fn.startswith("test"):
                    changed_test_funcs.append(f"{path}::{fn}")
            continue
        if path not in graph and not cf.is_new_file:
            # file isn't in the graph (unparseable / moved) — be conservative
            continue
        visited = {path}
        # parent maps node -> the node that imported it (for chain display)
        queue = [(path, [path])]
        while queue:
            node, chain = queue.pop(0)
            for importer in reverse.get(node, ()):
                if importer in visited:
                    continue
                visited.add(importer)
                new_chain = chain + [importer]
                if is_test_file(importer):
                    if len(new_chain) > 2:
                        via = " → ".join(new_chain[1:-1])
                        reason = f"diff touches {path}{sym_desc} (via {via})"
                    else:
                        reason = f"diff touches {path}{sym_desc}"
                    note(importer, reason, symbols)
                else:
                    queue.append((importer, new_chain))

    # deleted files: any test still importing them will break
    for cf in deleted:
        mod_names = set(_module_names(cf.path))
        for rel, entry_imports in _all_raw_imports(root_path).items():
            if mod_names & set(entry_imports) and is_test_file(rel):
                note(rel, f"imports deleted file {cf.path}", [])

    if not affected:
        return result

    # function-level precision: which test functions call the changed symbols?
    changed_test_funcs_set = set(changed_test_funcs)
    test_functions: list[str] = []
    file_level: list[str] = []
    for test_file in sorted(affected):
        direct = sorted(f for f in changed_test_funcs_set
                        if f.split("::")[0] == test_file)
        if direct:
            test_functions.extend(direct)
            continue
        funcs = _test_functions_referencing(root_path / test_file,
                                            symbol_sources.get(test_file, set()))
        if funcs:
            test_functions.extend(f"{test_file}::{f}" for f in sorted(set(funcs)))
        else:
            file_level.append(test_file)

    result.test_files = sorted(affected)
    result.test_functions = sorted(set(test_functions))
    result.reasons = affected
    if test_functions and not file_level:
        result.confidence = "high"
    elif test_functions:
        result.confidence = "medium"
    else:
        result.confidence = "medium"
    return result


def _all_raw_imports(root_path: Path) -> dict[str, list[str]]:
    cache_file = root_path / CACHE_NAME
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text())
            if data.get("version") == _CACHE_VERSION:
                return {rel: e.get("imports", [])
                        for rel, e in data.get("entries", {}).items()}
        except (json.JSONDecodeError, OSError):
            pass
    return {rel: _raw_imports(root_path / rel, rel)
            for rel in _discover_python_files(root_path)}
