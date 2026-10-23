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
# Wrapper programs that prefix another program. After a wrapper we keep
# expecting a program: skip its option words, NAME=value words (env), and for
# timeout the first non-option word (the duration); for sudo/doas skip the
# value of -u/-g/-C. The wrapper's own key is still added.
WRAPPER_PROGRAMS = frozenset({
    "env", "nice", "nohup", "timeout", "xargs", "sudo", "doas", "exec",
    "command", "builtin", "time", "stdbuf", "setsid", "ionice", "chroot", "busybox",
})
# Options of wrapper programs that take a separate value (next word).
_WRAPPER_OPTS_WITH_VALUE = {
    "nice": frozenset({"-n", "--adjustment"}),
    "ionice": frozenset({"-c", "-n", "-p", "--class", "--classdata", "--pid"}),
    "stdbuf": frozenset({"-i", "-o", "-e", "--input", "--output", "--error"}),
    "xargs": frozenset({
        "-n", "-P", "-I", "-L", "-s", "-d", "-a", "-E",
        "--max-args", "--max-procs", "--max-lines", "--max-chars",
        "--delimiter", "--arg-file",
    }),
    "timeout": frozenset({"-s", "--signal", "-k", "--kill-after"}),
    "env": frozenset({"-u", "--unset", "-C", "--chdir", "-S", "--split-string"}),
}
# Options of those programs that take a separate value (``git -C dir status``).
_OPTIONS_WITH_VALUE = frozenset({
    "-C", "-c", "--config-env",
    "--git-dir", "--work-tree", "--namespace", "--super-prefix",
})
# Git options that take no value and are safe to ignore before the subcommand.
_GIT_NO_VALUE_OPTS = frozenset({
    "--no-pager", "-p", "--paginate", "-P",
    "--bare", "--no-replace-objects",
    "--literal-pathspecs", "--glob-pathspecs", "--noglob-pathspecs", "--icase-pathspecs",
    "--no-optional-locks",
})
_SUBCOMMAND = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:+-]*")
# Writing a file through the shell (``>``, ``>>``, ``&>``) is its own rule:
# ``echo`` being allowed must not let ``echo x > ~/.bashrc`` through.
OUTPUT_REDIRECT = "output-redirect"
# ``git -c`` / ``--config-env`` / ``--exec-path`` can set an alias or pager that runs any
# command, so ``git -c status`` needs this rule on top of ``git status``.
GIT_CONFIG_OVERRIDE = "git -c"
_DUP_TARGET = re.compile(r"[0-9]+-?|-")


def _has_command_substitution_in_double_quotes(text: str) -> bool:
    """Return True if $(...), ${...} or backtick occurs inside double quotes.

    Tracks quote state: single quotes (no escapes), double quotes (backslash
    escapes allowed), and backslash escapes outside single quotes.
    """
    in_single = False
    in_double = False
    escape = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if escape:
            escape = False
            i += 1
            continue
        if ch == "\\":
            if not in_single:
                escape = True
            i += 1
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            i += 1
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            i += 1
            continue
        if in_double:
            # Check for $( or ${ or backtick
            if ch == "$" and i + 1 < n and text[i + 1] in "({":
                return True
            if ch == "`":
                return True
        i += 1
    return False


def commands(command: str) -> list[str] | None:
    """The rule keys of the commands a shell command runs, in order, without
    duplicates; None when it cannot be parsed. A key is the program's basename
    (``/usr/bin/curl`` is ``curl``), plus the subcommand for the programs in
    :data:`SUBCOMMAND_PROGRAMS` (``npm run``, ``git status``). Pipes, lists,
    subshells, ``$(...)``, backticks and newlines all start a new command."""
    # Fail closed if command substitution appears inside double quotes.
    if _has_command_substitution_in_double_quotes(command):
        return None

    text = command.replace("`", ";").replace("\n", ";")
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
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
    prog: str | None = None  # the program of the current simple command
    uniq_pos = 0  # positional argument count for uniq (2nd is output file)
    uniq_end_opts = False  # -- seen for uniq, everything after is positional
    # Wrapper state: when we are inside a wrapper program, what kind of skipping to do
    wrapper: str | None = None  # current wrapper program name
    wrapper_skip_duration = False  # for timeout: skip the first non-option (duration)
    wrapper_skip_root = False  # for chroot: skip the first non-option (root dir)
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
            if wrapper:
                wrapper = None
                wrapper_skip_duration = False
                wrapper_skip_root = False
            expect = True
            prog = None
            uniq_pos = 0
            uniq_end_opts = False
            skip_next = False
            redirect_target = False
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
            if sub_of == "git":
                if tok.startswith(("-c", "--config-env")):
                    add(GIT_CONFIG_OVERRIDE)
                elif tok.startswith("--exec-path"):
                    add(GIT_CONFIG_OVERRIDE)
                elif tok == "--output" or tok.startswith("--output="):
                    add(OUTPUT_REDIRECT)
                    if tok == "--output":
                        skip_next = True
                elif tok.startswith("-") and tok not in _OPTIONS_WITH_VALUE and tok not in _GIT_NO_VALUE_OPTS:
                    # Unknown git option before subcommand: fail closed.
                    return None
            if tok in _OPTIONS_WITH_VALUE:
                skip_next = True
            elif not tok.startswith("-"):
                add(f"{sub_of} {tok}" if _SUBCOMMAND.fullmatch(tok) else sub_of)
                sub_of = None
            continue
        if not expect or _ASSIGNMENT.fullmatch(tok) or tok in _KEYWORDS:
            # Handle --output, sort options, and uniq positional output in argument position
            if not expect:
                # sort --compress-program and its prefixes: fail closed
                if prog == "sort" and tok.startswith("--"):
                    opt = tok[2:].split("=", 1)[0]
                    if len(opt) >= 2 and "compress-program".startswith(opt):
                        return None
                    if len(opt) >= 1 and "output".startswith(opt):
                        add(OUTPUT_REDIRECT)
                        if "=" not in tok:
                            skip_next = True
                        continue
                # sort clustered short option containing o (e.g. -ro, -o)
                if prog == "sort" and tok.startswith("-") and not tok.startswith("--") and "o" in tok[1:]:
                    add(OUTPUT_REDIRECT)
                    skip_next = True
                    continue
                # uniq positional argument counting: 2nd positional is output file
                if prog == "uniq":
                    if uniq_end_opts or tok == "-" or not tok.startswith("-"):
                        # positional argument (after --, bare -, or non-option)
                        uniq_pos += 1
                        if uniq_pos == 2:
                            add(OUTPUT_REDIRECT)
                        continue
                    if tok == "--":
                        uniq_end_opts = True
                        continue
                    if tok.startswith("--"):
                        # long option: --skip-fields, --skip-chars, --check-chars
                        opt = tok[2:].split("=", 1)[0]
                        if "=" not in tok and opt and any(
                            long_opt.startswith(opt) for long_opt in ("skip-fields", "skip-chars", "check-chars")
                        ):
                            skip_next = True
                        continue
                    # short cluster: -c, -f, -f1, -cf1, -cf, etc.
                    for i, ch in enumerate(tok[1:]):
                        if ch in "fsw":
                            if i == len(tok) - 2:
                                skip_next = True
                            # else the rest of tok is the attached value
                            break
                        continue
                # general --output handling (git diff, etc.)
                if tok == "--output" or tok.startswith("--output="):
                    add(OUTPUT_REDIRECT)
                    if tok == "--output":
                        skip_next = True
                    continue
            continue
        name = os.path.basename(tok) or tok
        # Handle wrapper programs: they prefix another program
        if name in WRAPPER_PROGRAMS:
            add(name)
            wrapper = name
            # For timeout, the first non-option after options is the duration (skip it)
            if name == "timeout":
                wrapper_skip_duration = True
            # For chroot, the first non-option after options is the root dir (skip it)
            if name == "chroot":
                wrapper_skip_root = True
            else:
                wrapper_skip_root = False
            # For sudo/doas, we need to skip values of -u, -g, -C
            # For env, NAME=value assignments are handled by _ASSIGNMENT check above
            # For other wrappers, just skip options (words starting with -)
            expect = True  # keep expecting the real program
            prog = name
            uniq_pos = 0
            uniq_end_opts = False
            continue
        # If we are in a wrapper context, skip its options
        if wrapper:
            # sudo/doas: skip -u user, -g group, -C dir (and their long forms)
            if wrapper in ("sudo", "doas"):
                if tok in ("-u", "-g", "-C", "--user", "--group", "--chdir"):
                    skip_next = True
                    continue
                if tok.startswith(("-u", "-g", "-C")) and len(tok) > 2:
                    # -uuser, -ggroup, -Cdir (attached value)
                    continue
            # timeout: skip the first non-option (duration) after options
            if wrapper == "timeout" and wrapper_skip_duration:
                if not tok.startswith("-"):
                    wrapper_skip_duration = False
                    continue
            # chroot: skip the first non-option (root dir) after options
            if wrapper == "chroot" and wrapper_skip_root:
                if not tok.startswith("-"):
                    wrapper_skip_root = False
                    continue
            # Wrapper-specific options that take a separate value
            if wrapper in _WRAPPER_OPTS_WITH_VALUE:
                if tok in _WRAPPER_OPTS_WITH_VALUE[wrapper]:
                    skip_next = True
                    continue
            # Other wrappers: skip options (words starting with -)
            if tok.startswith("-"):
                continue
            # This token is the real program
            wrapper = None
            wrapper_skip_duration = False
            wrapper_skip_root = False
            # Fall through to process as normal program
        expect = False
        prog = name
        uniq_pos = 0
        uniq_end_opts = False
        if name in SUBCOMMAND_PROGRAMS:
            sub_of = name
        else:
            add(name)
    if sub_of:
        add(sub_of)
    return found


def _covered(key: str, rules: set[str]) -> bool:
    """A rule for the bare program covers every subcommand of it.

    Exception: GIT_CONFIG_OVERRIDE and OUTPUT_REDIRECT match only an exact rule.
    """
    if key in (GIT_CONFIG_OVERRIDE, OUTPUT_REDIRECT):
        return key in rules
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
    # Hard refusal on raw text (catches gh/git push in simple cases)
    if _GH.search(command) or _GIT_PUSH.search(command):
        return REFUSAL
    # Hard refusal on parsed command keys (catches g""h, 'gh', etc.)
    keys = commands(command)
    if keys:
        for key in keys:
            if key == "gh" or key.startswith("gh ") or key == "git push":
                return REFUSAL
    return None


def _arg(argv: list[str], flag: str) -> str | None:
    return argv[argv.index(flag) + 1] if flag in argv[:-1] else None


def main(argv: list[str] | None = None) -> int:
    try:
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
    except Exception:
        # Fail closed: any unexpected error blocks the call
        print(REFUSAL, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
