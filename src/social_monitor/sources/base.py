"""Abstract base class for all source plugins."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

from social_monitor.models import Post


@dataclass
class ConfigField:
    """Describes a single configuration field for a source plugin.

    The settings UI uses these to dynamically generate forms.
    """

    key: str  # Key in the settings dict
    label: str  # Human-readable label
    field_type: Literal["str", "password", "int", "str_list", "int_list", "text"]
    default: Any = ""
    placeholder: str = ""
    help_text: str = ""
    required: bool = False


@dataclass
class AccessMethod:
    """Describes one way to access a source (e.g., RSS, OAuth, Scrape)."""

    key: str  # Stored in config, e.g. "rss", "oauth", "scrape"
    label: str  # Shown in dropdown, e.g. "RSS Feed", "OAuth API"
    description: str  # Help text shown below dropdown
    fields: list[ConfigField]  # Config fields specific to this method


class BaseSource(ABC):
    """Interface that every source plugin must implement."""

    name: str  # Unique source type identifier
    display_name: str  # Human-readable name shown in UI
    description: str  # Short description of what this source monitors
    default_interval: int = 120

    @classmethod
    @abstractmethod
    def supported_methods(cls) -> list[AccessMethod]:
        """Declare the access methods this source supports.

        Each method has its own set of config fields. The settings UI
        shows a dropdown to pick the method, and renders the matching
        fields below it.

        The first method in the list is the default.
        """

    @classmethod
    def common_fields(cls) -> list[ConfigField]:
        """Config fields shared across all methods for this source.

        Override in subclasses if there are fields that apply regardless
        of the chosen access method. These render above the method dropdown.
        """
        return []

    @classmethod
    def default_method(cls) -> str:
        """Return the key of the default access method."""
        methods = cls.supported_methods()
        return methods[0].key if methods else ""

    @abstractmethod
    async def setup(self, config: dict) -> None:
        """Initialize connections, validate credentials."""

    @abstractmethod
    async def fetch_new(self) -> list[Post]:
        """Return new posts since last check."""

    @abstractmethod
    async def teardown(self) -> None:
        """Clean up connections."""

    def validate_config(self, config: dict) -> list[str]:
        """Return a list of config validation errors (empty = valid)."""
        errors = []
        method_key = config.get("method", self.default_method())
        methods = {m.key: m for m in self.supported_methods()}
        method = methods.get(method_key)
        if method is None:
            errors.append(f"{self.display_name}: unknown method '{method_key}'")
            return errors

        settings = config.get("settings", {})
        for f in self.common_fields() + method.fields:
            if f.required and not settings.get(f.key):
                errors.append(f"{self.display_name}: {f.label} is required")
        return errors
