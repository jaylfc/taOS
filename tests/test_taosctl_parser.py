"""Cross-cutting integration test for the taosctl CLI parser.

Catches in one place the regression where a noun module under
``tinyagentos/cli/taosctl/commands`` fails to expose the ``NOUN``/``register``
contract that ``build_parser`` relies on (and that ``iter_noun_modules`` silently
skips), and where a representative argv no longer resolves a handler.

Uses only the real ``build_parser()``: no network, no ``TaosClient`` calls.
"""
from __future__ import annotations

import importlib
import pkgutil

import pytest

from tinyagentos.cli.taosctl.commands import iter_noun_modules
from tinyagentos.cli.taosctl.__main__ import build_parser

COMMANDS_PKG = "tinyagentos.cli.taosctl.commands"


def _command_modules():
    """Yield every importable module in commands/ except __init__ and _-privates."""
    pkg = importlib.import_module(COMMANDS_PKG)
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("_"):
            continue
        yield info.name, importlib.import_module(f"{COMMANDS_PKG}.{info.name}")


def test_every_noun_module_exposes_noun_and_register():
    modules = dict(_command_modules())
    assert modules, "expected at least one noun module under commands/"
    missing_no_noun = [name for name, mod in modules.items() if not hasattr(mod, "NOUN")]
    missing_register = [
        name for name, mod in modules.items() if not hasattr(mod, "register")
    ]
    not_callable = [
        name for name, mod in modules.items()
        if hasattr(mod, "register") and not callable(mod.register)
    ]
    assert not missing_no_noun, f"modules missing NOUN: {missing_no_noun}"
    assert not missing_register, f"modules missing register(): {missing_register}"
    assert not not_callable, f"register is not callable in: {not_callable}"


def test_iter_noun_modules_covers_every_command_module():
    """A noun that fails the contract is silently dropped by iter_noun_modules,
    so its output must match the full set of command modules one for one."""
    declared = {name for name, _ in _command_modules()}
    discovered = {mod.__name__.rsplit(".", 1)[-1] for mod in iter_noun_modules()}
    assert discovered == declared, (
        f"iter_noun_modules() does not cover every command module; "
        f"missing={sorted(declared - discovered)} extra={sorted(discovered - declared)}"
    )


def test_noun_values_are_unique_and_nonempty():
    nouns = [mod.NOUN for mod in iter_noun_modules()]
    assert len(nouns) == len(set(nouns)), f"duplicate NOUN values: {nouns}"
    assert all(n for n in nouns), "NOUN must be a non-empty string"


@pytest.mark.parametrize("argv", [
    ["projects", "list"],
    ["agents", "list"],
    ["apps", "get", "x"],
    ["tasks", "update", "1", "--enabled", "true"],
])
def test_valid_commands_resolve_a_handler(argv):
    parser = build_parser()
    args = parser.parse_args(argv)
    assert getattr(args, "func", None) is not None, (
        f"argv {argv!r} did not resolve a handler (args.func unset)"
    )
    assert callable(args.func), f"args.func for {argv!r} is not callable"


def test_parser_has_required_noun_and_version():
    parser = build_parser()
    assert parser.prog == "taosctl"
    # A noun is mandatory: parsing without one fails rather than returning a
    # namespace with no verb subparser set.
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_unknown_noun_is_rejected():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["definitely-not-a-noun", "list"])
