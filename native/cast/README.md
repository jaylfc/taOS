# taos-cast

Cast the taOS desktop to a TV (AirPlay through tvpipe) and always put the monitor back.

    taos-cast start --to "Bedroom TV" [--size 1920x1080] [--fps 30] [--tvpipe PATH] [--no-input]
                    [--lock-cmd CMD] [--unlock-cmd CMD]
    taos-cast stop | status | restore | pair

`start` creates a virtual sway output at the given size, sets its wallpaper (`taos-wallpaper path --size`
when that binary exists, else a dark solid colour), checks it is active, then switches the previously
active outputs off and runs `wf-recorder | (built-in SLRW adapter) | tvpipe play --raw-video`. Encoder
settings are the proven constant-quality ones (CRF 20, capped, no B-frames, keyframe every 2 s, BT.709).
The size is never probed from the receiver; pass `--size` explicitly.

## Safety

* The restore state (`$XDG_RUNTIME_DIR/taos-cast/state.json`, mode 0600) is written before the first change.
* A supervisor process owns the pipeline and restores on SIGTERM, SIGINT, SIGHUP, or when the recorder or
  tvpipe exits.
* A separate watchdog process (own session) holds the read end of a pipe from the supervisor. If the
  supervisor dies in any way (including kill -9) the pipe closes and the watchdog restores.
* `restore` is idempotent and also callable by hand; only processes recorded with their start time are signalled.
* Lock: the session lock procedure is not specified yet (a session-wide locker would also cover the TV).
  Only the monitor is switched off. `--lock-cmd` and `--unlock-cmd` are explicit opt-in hooks. The owner decides.

## Controller

Unless `--no-input`, `start` talks to the taos-input control socket (`$XDG_RUNTIME_DIR/taos-input/control.sock`,
override with `TAOS_INPUT_CONTROL`): JSON line request, one JSON line reply. It starts `taos-input serve`
if nothing answers (and stops it again only if it started it), asks `{"op":"pair"}` for a code, prints the
controller URL and code, and shows them on the TV with `swaynag` (best effort). `taos-cast pair` asks for a fresh code.

## Agent path

Add `--json`: exactly one JSON object on stdout, exit code 0 on success and 1 on failure.

| command | reply |
|---|---|
| start | `{"ok":true,"status":"running","receiver","output","size","fps","url","code"}` |
| status | `{"ok":true,"status":"running\|starting\|idle","alive":bool,...}`; idle carries `last` (`status`, `reason`) of the previous cast |
| stop | `{"ok":true,"status":"stopped\|idle\|restored"}` |
| restore | `{"ok":true,"restored":bool}` |
| pair | the engine reply: `{"ok":true,"code","expires_in","url"}` |
| any failure | `{"ok":false,"error":"..."}` |

## Tests

`cargo test -p taos-cast`. The e2e tests need `sway` and run in a private headless session with two
headless outputs as stand-in monitors and stub `wf-recorder`/`tvpipe`; set `TAOS_E2E=1` to fail instead of skip when sway is missing.
