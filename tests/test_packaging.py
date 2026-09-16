"""Packaging guards for the distributed wheel.

The API services are deployed from a wheel built off pyproject.toml's explicit
package list. A module inside a shipped package that imports a repository
directory which is NOT shipped (scripts/, tests/, alembic/) works in a source
checkout — where that directory sits on sys.path — and fails only after
installation. That exact gap once broke `ai-hunter-api` at startup.
"""

from __future__ import annotations

import ast
import re
import pathlib
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Directories that exist in the repository but are deliberately not shipped.
NOT_SHIPPED = {"scripts", "tests", "alembic", "deploy", "docs"}


def _declared_packages() -> set[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return set(data["tool"]["setuptools"]["packages"])


def _declared_dependencies() -> set[str]:
    """Top-level names pinned in requirements.txt.

    A repository directory and a third-party distribution can share a name —
    `alembic/` holds the migrations while `alembic` is also an installed
    library — so imports of those names are legitimate.
    """
    names: set[str] = set()
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        names.add(re.split(r"[<>=!\[; ]", line, maxsplit=1)[0].strip().lower())
    return names


def test_pyproject_lists_every_top_level_package():
    """Every importable top-level package in the repo must be declared."""
    on_disk = {
        p.name
        for p in ROOT.iterdir()
        if p.is_dir() and (p / "__init__.py").is_file() and not p.name.startswith(".")
    }
    declared = _declared_packages()
    missing = on_disk - declared - NOT_SHIPPED
    assert not missing, f"pyproject.toml packages is missing: {sorted(missing)}"


def test_shipped_packages_do_not_import_unshipped_directories():
    """Shipped code must not import scripts/, tests/ or alembic/."""
    declared = _declared_packages()
    third_party = _declared_dependencies()
    violations: list[str] = []

    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT)
        if rel.parts[0] not in declared:  # only inspect code that ships
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - defensive
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top in NOT_SHIPPED and top not in third_party:
                        violations.append(f"{rel}:{node.lineno} import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                top = node.module.split(".")[0]
                if top in NOT_SHIPPED and top not in third_party:
                    violations.append(f"{rel}:{node.lineno} from {node.module} import ...")

    assert not violations, (
        "shipped modules import directories that are not in the wheel "
        f"(move the shared symbol into a shipped package): {violations}"
    )


def test_entry_points_are_importable_and_declared():
    """The two services the systemd units start must resolve."""
    import api.hunter_app
    import api.marketing_app

    assert callable(api.hunter_app.create_hunter_app)
    assert callable(api.marketing_app.create_marketing_app)
