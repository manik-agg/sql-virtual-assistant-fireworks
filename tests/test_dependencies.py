"""F-16: every third-party package the code imports is declared, and nothing declared is unused."""

import ast
import re
import sys
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCAL = {"src", "eval", "tests", "conftest", "audit"}


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def declared() -> tuple[set[str], set[str]]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())

    def pick(specs):
        return {norm(re.split(r"[<>=!~\s\[]", s, maxsplit=1)[0]) for s in specs}

    return pick(data["project"]["dependencies"]), pick(data.get("dependency-groups", {}).get("dev", []))


def imported_distributions() -> dict[str, set[str]]:
    dists = packages_distributions()
    found: dict[str, set[str]] = {}
    for folder in ("src", "eval", "tests"):
        for path in (ROOT / folder).glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                mods = (
                    [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module]
                    if isinstance(node, ast.ImportFrom) and node.module and node.level == 0
                    else []
                )
                for mod in mods:
                    top = mod.split(".")[0]
                    if top in sys.stdlib_module_names or top in LOCAL:
                        continue
                    for dist in dists.get(top, [top]):
                        found.setdefault(norm(dist), set()).add(f"{folder}/{path.name}")
    return found


def test_every_imported_package_is_declared():
    main, dev = declared()
    missing = {d: sorted(f)[:2] for d, f in imported_distributions().items() if d not in main | dev}
    assert not missing, missing


def test_no_declared_runtime_dependency_is_unused():
    main, _ = declared()
    unused = sorted(d for d in main if d not in imported_distributions())
    assert not unused, unused
