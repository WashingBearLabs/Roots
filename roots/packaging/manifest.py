"""Root package manifest schema for describing packaged Roots."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, field_validator


_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

_PACKAGE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+(/[a-zA-Z0-9_-]+)?$")


class AgentContract(BaseModel):
    """Describes an agent slot that a Root package expects to be filled."""

    name: str
    description: str | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    required: bool = True
    timeout_seconds: int = 300
    tags: list[str] = []

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must not be empty")
        return v


class ConfigOverride(BaseModel):
    """A tunable parameter exposed by the package."""

    path: str
    description: str
    default_value: Any
    value_type: str
    constraints: dict[str, Any] | None = None


class ConfigTemplate(BaseModel):
    """A named preset of configuration overrides."""

    name: str
    description: str
    overrides: dict[str, Any]

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must not be empty")
        return v


class RootManifest(BaseModel):
    """Top-level manifest describing a packaged Root."""

    format_version: str = "1.0"
    package_id: str
    package_version: str
    name: str
    description: str
    author: str | None = None
    license: str | None = None
    tags: list[str] = []
    roots_version: str
    process_file: str = "process.yaml"
    agent_contracts: list[AgentContract]
    config_overrides: list[ConfigOverride] = []
    config_templates: list[ConfigTemplate] = []
    has_defaults: bool = False
    defaults_module: str | None = None
    readme_file: str | None = "README.md"
    checksum: str | None = None

    @field_validator("defaults_module")
    @classmethod
    def validate_defaults_module(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not re.match(r"^defaults(\.[a-zA-Z_][a-zA-Z0-9_]*)*$", v):
            raise ValueError(
                f"defaults_module must start with 'defaults' and contain only "
                f"valid Python identifiers: {v!r}"
            )
        return v

    @field_validator("process_file")
    @classmethod
    def validate_process_file(cls, v: str) -> str:
        if ".." in v or v.startswith("/"):
            raise ValueError(
                f"process_file must be a simple relative path: {v!r}"
            )
        return v

    @field_validator("package_id")
    @classmethod
    def validate_package_id(cls, v: str) -> str:
        if not _PACKAGE_ID_RE.match(v):
            raise ValueError(
                f"package_id must match 'org/name' or 'name' "
                f"(alphanumeric, hyphens, underscores): {v!r}"
            )
        return v

    @field_validator("package_version")
    @classmethod
    def validate_package_version(cls, v: str) -> str:
        if not _SEMVER_RE.match(v):
            raise ValueError(f"package_version must be valid semver: {v!r}")
        return v
