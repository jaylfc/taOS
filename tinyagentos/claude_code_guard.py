"""PreToolUse hook for the Claude Code harness of the system taOS Agent.

Claude Code runs this script before every shell tool call (wired by the
settings file :mod:`tinyagentos.claude_code_runtime` passes with ``--settings``).
It refuses ``git push`` and any ``gh`` call so the only route from the agent
to GitHub is the gated taOS endpoint. Exit 2 blocks the call and hands stderr
back to the model; unreadable input is refused too (fail closed).

With ``--rules <file> --asks <file>`` it also applies the user's shell
profile: every command it runs (the program, plus the subcommand for tools
like ``npm`` or ``git``) must be on the profile's allow list. A denied one is
refused; an undecided one is refused and appended to the asks file, which taOS turns into an approve/deny Decision for the user after
the turn.

Run as a plain script (``python claude_code_guard.py``): stdlib only, so it
never imports taOS.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys

REFUSAL = ("Refused by taOS: the agent may not run `git push` or `gh` from the shell. "
           "GitHub writes go through the taOS tool, which asks the user to confirm.")

# Word edges that also treat path separators as boundaries, so `/usr/bin/gh`
# and `bash -c 'git push'` match while `ghost` or `git-pushed` do not.
_GH = re.compile(r"(?<![\w.-])gh(?![\w.-])")
_GIT_PUSH = re.compile(r"(?<![\w.-])git(?![\w.-])[^;&|\n]*?(?<![\w.-])push(?![\w.-])")


_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*", re.DOTALL)
_OPERATOR_CHARS = set("();&|<>")
# Shell words that start a command position without being a program.
_KEYWORDS = frozenset({"$", "!", "{", "}", "if", "then", "else", "elif", "fi",
                       "do", "done", "while", "until", "case", "esac"})
# Programs whose first non-option word is a subcommand: a rule for them can
# name the subcommand too (``npm test``, ``git status``).
SUBCOMMAND_PROGRAMS = frozenset({
    "apt", "apt-get", "brew", "cargo", "dnf", "docker", "git", "go", "kubectl", "make",
    "npm", "npx", "pip", "pip3", "pnpm", "podman", "snap", "systemctl", "uv", "yarn",
})
# Options of those programs that take a separate value (``git -C dir status``).
_OPTIONS_WITH_VALUE = frozenset({"-C", "-c", "--config-env"})
_SUBCOMMAND = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:+-]*")
# Writing a file through the shell (``>``, ``>>``, ``&>``) is its own rule:
# ``echo`` being allowed must not let ``echo x > ~/.bashrc`` through.
OUTPUT_REDIRECT = "output-redirect"
# ``git -c`` / ``--config-env`` can set an alias or pager that runs any
# command, so ``git -c status`` needs this rule on top of ``git status``.
GIT_CONFIG_OVERRIDE = "git -c"
_DUP_TARGET = re.compile(r"[0-9]+-?|-")


def commands(command: str) -> list[str] | None:
    """The rule keys of the commands a shell command runs, in order, without
    duplicates; None when it cannot be parsed. A key is the program's basename
    (``/usr/bin/curl`` is ``curl``), plus the subcommand for the programs in
    :data:`SUBCOMMAND_PROGRAMS` (``npm run``, ``git status``). Pipes, lists,
    subshells, ``$(...)``, backticks and newlines all start a new command."""
    text = command.replace("`", ";").replace("\n", ";")
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return None
    found: list[str] = []

    def add(key: str) -> None:
        if key not in found:
            found.append(key)

    expect = True
    skip_next = False
    redirect_target = False  # the next word is the target of an output redirection
    sub_of: str | None = None  # a program still waiting for its subcommand
    for tok in tokens:
        if tok and set(tok) <= _OPERATOR_CHARS:
            if set(tok) & set("<>") and not set(tok) & set(";|()"):
                # A redirection: the next word is a file (or an fd for >&).
                if ">" in tok:
                    redirect_target = True
                else:
                    skip_next = True
                continue
            if sub_of:
                add(sub_of)
                sub_of = None
            expect = True
            continue
        if redirect_target:
            redirect_target = False
            if tok != "/dev/null" and not _DUP_TARGET.fullmatch(tok):
                add(OUTPUT_REDIRECT)
            continue
        if skip_next:
            skip_next = False
            continue
        if sub_of:
            if sub_of == "git" and (tok.startswith(("-c", "--config-env"))):
                add(GIT_CONFIG_OVERRIDE)
            if tok in _OPTIONS_WITH_VALUE:
                skip_next = True
            elif not tok.startswith("-"):
                add(f"{sub_of} {tok}" if _SUBCOMMAND.fullmatch(tok) else sub_of)
                sub_of = None
            continue
        if not expect or _ASSIGNMENT.fullmatch(tok) or tok in _KEYWORDS:
            continue
        name = os.path.basename(tok) or tok
        expect = False
        if name in SUBCOMMAND_PROGRAMS:
            sub_of = name
        else:
            add(name)
    if sub_of:
        add(sub_of)
    return found


def _covered(key: str, rules: set[str]) -> bool:
    """A rule for the bare program covers every subcommand of it."""
    return key in rules or key.split(" ", 1)[0] in rules


def profile_refusal(command: str, allow: set[str], deny: set[str]) -> tuple[str | None, list[str]]:
    """Check a shell command against a profile: the refusal message (or None)
    and the undecided commands to ask the user about."""
    keys = commands(command)
    if keys is None:
        return "Refused by taOS: this shell command could not be parsed, so it cannot be checked.", []
    denied = [k for k in keys if _covered(k, deny)]
    if denied:
        return (f"Refused by taOS: the user has denied `{denied[0]}` for the agent. "
                "Do not retry it; find another way or ask the user."), []
    unknown = [k for k in keys if not _covered(k, allow)]
    if unknown:
        listed = ", ".join(f"`{k}`" for k in unknown)
        return (f"Refused by taOS: the user has not decided yet whether the agent may run {listed}. "
                "taOS has asked them in the chat and in Decisions. Tell the user, and retry "
                "after they allow it."), unknown
    return None, []


def refusal(payload: object) -> str | None:
    """The refusal message for one PreToolUse payload, or None to allow it."""
    if not isinstance(payload, dict):
        return REFUSAL
    if payload.get("tool_name") != "Bash":
        return None
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return REFUSAL
    if _GH.search(command) or _GIT_PUSH.search(command):
        return REFUSAL
    return None


def _arg(argv: list[str], flag: str) -> str | None:
    return argv[argv.index(flag) + 1] if flag in argv[:-1] else None


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        payload = json.loads(sys.stdin.read())
    except ValueError:
        payload = None
    message = refusal(payload)
    rules_path = _arg(argv, "--rules")
    if message is None and rules_path and payload.get("tool_name") == "Bash":
        command = payload["tool_input"]["command"]
        try:
            with open(rules_path, encoding="utf-8") as f:
                rules = json.load(f)
            allow, deny = set(rules.get("allow") or ()), set(rules.get("deny") or ())
        except (OSError, ValueError, AttributeError, TypeError):
            print("Refused by taOS: the shell profile could not be read.", file=sys.stderr)
            return 2
        message, unknown = profile_refusal(command, allow, deny)
        asks_path = _arg(argv, "--asks")
        if unknown and asks_path:
            try:
                with open(asks_path, "a", encoding="utf-8") as f:
                    f.writelines(json.dumps({"program": key, "command": command}) + "\n"
                                 for key in unknown)
            except OSError:
                pass
    if message is None:
        return 0
    print(message, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
