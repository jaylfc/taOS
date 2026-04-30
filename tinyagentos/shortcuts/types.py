from __future__ import annotations

from typing import Literal, Optional, Union
from typing_extensions import TypedDict


class ShortcutCommon(TypedDict):
    label: str
    icon: str
    requires_capability: str


class ContainerTerminalShortcut(ShortcutCommon):
    kind: Literal["container-terminal"]


class TuiShortcut(ShortcutCommon):
    kind: Literal["tui"]
    command: str


class _TokenSourceContainerFile(TypedDict):
    kind: Literal["container_file"]
    path: str
    json_pointer: str


class _TokenSourceContainerEnv(TypedDict):
    kind: Literal["container_env"]
    var: str


class _TokenSourceStatic(TypedDict):
    kind: Literal["static"]
    value: str


TokenSource = Union[
    _TokenSourceContainerFile,
    _TokenSourceContainerEnv,
    _TokenSourceStatic,
]


class DashboardAuth(TypedDict):
    type: Literal["none", "bearer", "basic"]
    token_source: Optional[TokenSource]


class DashboardShortcut(ShortcutCommon):
    kind: Literal["dashboard"]
    port: int
    path: str
    auth: DashboardAuth


Shortcut = Union[ContainerTerminalShortcut, TuiShortcut, DashboardShortcut]
