"""Check new dependencies against PyPI.

Catches hallucinated packages (404 on PyPI), brand-new low-usage packages
(typosquat risk), and suggests the popular package you probably meant.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

PYPI_URL = "https://pypi.org/pypi/{package}/json"
STATS_URL = "https://pypistats.org/api/packages/{package}/recent"

POPULAR_PACKAGES = [
    "requests", "numpy", "pandas", "flask", "django", "pyjwt", "sqlalchemy",
    "click", "rich", "httpx", "pytest", "boto3", "scipy", "matplotlib",
    "pillow", "beautifulsoup4", "cryptography", "paramiko", "aiohttp",
    "fastapi", "pydantic", "urllib3", "certifi", "setuptools", "wheel",
    "torch", "tensorflow", "scikit-learn", "transformers", "openai",
    "anthropic", "redis", "celery", "gunicorn", "uvicorn", "starlette",
    "jinja2", "pyyaml", "lxml", "psycopg2", "pymysql", "pymongo",
    "python-dateutil", "tqdm", "six", "attrs", "packaging",
    "typing-extensions", "colorama", "toml", "pytest-cov", "mock",
    "responses", "faker", "factory-boy", "django-rest-framework",
    "djangorestframework", "arrow", "pendulum", "typer", "fire",
]


@dataclass
class DepRisk:
    package: str
    exists: bool | None          # None = couldn't verify (network error)
    age_days: int | None = None
    weekly_downloads: int | None = None
    risk_level: str = "safe"     # "safe" | "suspicious" | "dangerous"
    similar_packages: list[str] = field(default_factory=list)
    reason: str = ""


def _classify(exists: bool | None, age_days: int | None,
              weekly_downloads: int | None) -> tuple[str, str]:
    if exists is False:
        return "dangerous", "package does not exist on PyPI (likely hallucinated)"
    if exists is None:
        return "suspicious", "could not verify package on PyPI (network error)"
    if age_days is not None and weekly_downloads is not None:
        if age_days < 30 and weekly_downloads < 100:
            return ("dangerous",
                    f"registered {age_days} days ago with "
                    f"{weekly_downloads} weekly downloads")
        if age_days < 90 and weekly_downloads < 1000:
            return ("suspicious",
                    f"registered {age_days} days ago with "
                    f"{weekly_downloads} weekly downloads")
    parts = []
    if age_days is not None:
        parts.append(f"on PyPI for {age_days} days")
    if weekly_downloads is not None:
        parts.append(f"{weekly_downloads} weekly downloads")
    return "safe", ", ".join(parts) or "established package"


def _similar(package: str) -> list[str]:
    return difflib.get_close_matches(
        package.lower(), POPULAR_PACKAGES, n=3, cutoff=0.72
    )


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def check_dependencies(packages: list[str], timeout: float = 10.0) -> list[DepRisk]:
    risks: list[DepRisk] = []
    if not packages:
        return risks
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for pkg in packages:
            risk = DepRisk(package=pkg, exists=None)
            try:
                resp = client.get(PYPI_URL.format(package=pkg))
                if resp.status_code == 404:
                    risk.exists = False
                elif resp.status_code == 200:
                    risk.exists = True
                    data = resp.json()
                    earliest: datetime | None = None
                    for releases in data.get("releases", {}).values():
                        for f in releases:
                            t = _parse_time(f.get("upload_time", ""))
                            if t and (earliest is None or t < earliest):
                                earliest = t
                    if earliest:
                        risk.age_days = max(
                            0, (datetime.now(timezone.utc) - earliest).days
                        )
            except httpx.HTTPError:
                risk.exists = None

            if risk.exists:
                try:
                    sresp = client.get(STATS_URL.format(package=pkg.lower()))
                    if sresp.status_code == 200:
                        risk.weekly_downloads = (
                            sresp.json().get("data", {}).get("last_week")
                        )
                except (httpx.HTTPError, ValueError):
                    pass

            risk.risk_level, risk.reason = _classify(
                risk.exists, risk.age_days, risk.weekly_downloads
            )
            if risk.risk_level != "safe" or risk.exists is False:
                risk.similar_packages = _similar(pkg)
            risks.append(risk)
    return risks
