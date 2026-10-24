"""`config.json`: what to open and what to listen to.

The windows are two lists:

  `dashboard`  the app dashboards, opened by name
  `pages`      pages opened by URL (volatility, surface…)

Each one declares its **targets**: the API paths we care about. Everything
else is noise and is neither captured nor decrypted.

The `active` field is written by the program: it becomes `true` when the
window is ready and listening, `false` if opening fails.

Translation of `src/config.rs`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_FILE = "config.json"


class ConfigError(Exception):
    """The configuration file cannot be read, or has the wrong shape."""


@dataclass
class Page:
    """A page opened by URL."""

    url: str
    targets: list[str] = field(default_factory=list)

    # Whether the page has the button that moves time forward: once it is
    # loaded, the program presses it.
    next: bool = False

    # Listening state: the program updates it.
    active: bool = False


@dataclass
class Dashboard:
    """An app dashboard, opened by name."""

    name: str
    targets: list[str] = field(default_factory=list)

    # Listening state: the program updates it.
    active: bool = False


@dataclass
class Config:
    url_od: str
    email_od: str
    password_od: str
    backend: str

    pages: dict[str, Page]
    dashboard: dict[str, Dashboard]

    # Where it was read from: the file is rewritten **here**, never
    # anywhere else.
    path: str = CONFIG_FILE

    # The file as it is, to rewrite it without losing fields.
    raw: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: str = CONFIG_FILE) -> Config:
        try:
            content = Path(path).read_text(encoding="utf-8")
        except OSError as error:
            raise ConfigError(f"Cannot read {path}: {error}") from error

        return cls.from_content(path, content)

    @classmethod
    def from_content(cls, path: str, content: str) -> Config:
        try:
            raw = json.loads(content)
        except json.JSONDecodeError as error:
            raise ConfigError(f"{CONFIG_FILE} is not valid JSON: {error}") from error

        if not isinstance(raw, dict):
            raise ConfigError(f"{CONFIG_FILE} has an unexpected shape")

        try:
            pages = {
                key: Page(
                    url=value["url"],
                    targets=list(value.get("targets", [])),
                    next=bool(value.get("next", False)),
                    active=bool(value.get("active", False)),
                )
                for key, value in raw.get("pages", {}).items()
            }

            dashboard = {
                key: Dashboard(
                    name=value["name"],
                    targets=list(value.get("targets", [])),
                    active=bool(value.get("active", False)),
                )
                for key, value in raw.get("dashboard", {}).items()
            }

            return cls(
                url_od=raw["url_od"],
                email_od=raw["email_od"],
                password_od=raw["password_od"],
                backend=raw.get("backend", "https://backend.optionsdepth.com"),
                pages=pages,
                dashboard=dashboard,
                path=path,
                raw=raw,
            )
        except (KeyError, TypeError, AttributeError) as error:
            raise ConfigError(f"{CONFIG_FILE} has an unexpected shape: {error}") from error

    # ------------------------------------------------------------------

    def backend_host(self) -> str:
        """The backend host, without scheme."""
        return host_of(self.backend)

    def previous_state(self) -> list[tuple[str, bool]]:
        """The `active` state left behind by the previous run."""
        state = [(key, item.active) for key, item in self.dashboard.items()]

        state += [(key, item.active) for key, item in self.pages.items()]

        return state

    def set_active(self, section: str, key: str, active: bool) -> None:
        """Writes `active` into the file, leaving everything else as it is.

        The file is rewritten on every state change: it is the tab that
        says, at a glance, which windows are listening.
        """
        entry = self.raw.get(section, {}).get(key)

        if not isinstance(entry, dict):
            return

        entry["active"] = active

        # The state in memory must follow the written one, or the next
        # read would say something stale.
        if section == "dashboard" and key in self.dashboard:
            self.dashboard[key].active = active

        if section == "pages" and key in self.pages:
            self.pages[key].active = active

        self.save()

    def save(self, path: str | None = None) -> None:
        """Rewrites the file with the same style: four-space indentation."""
        target = path or self.path

        text = json.dumps(self.raw, ensure_ascii=False, indent=4)

        Path(target).write_text(text + "\n", encoding="utf-8")


# ======================================================
# TARGETS
# ======================================================


def host_of(url: str) -> str:
    """`https://backend.optionsdepth.com` → `backend.optionsdepth.com`."""
    without_scheme = url.split("://", 1)[1] if "://" in url else url

    return without_scheme.replace("?", "/").replace("#", "/").split("/")[0]


def url_path(url: str) -> str:
    """The path of a URL, without host and without query."""
    without_scheme = url.split("://", 1)[1] if "://" in url else url

    path = without_scheme[without_scheme.find("/") :] if "/" in without_scheme else "/"

    return path.split("?")[0].split("#")[0]


def matches(targets: list[str], backend_host: str, url: str) -> bool:
    """Is the URL one of those to listen to in this window?

    Rules: host equal to the backend, path that starts with one of the
    targets. With no targets nothing is listened to.
    """
    if host_of(url) != backend_host:
        return False

    path = url_path(url)

    for target in targets:
        trimmed = target.rstrip("/")

        if path == trimmed or path.startswith(f"{trimmed}/"):
            return True

    return False
