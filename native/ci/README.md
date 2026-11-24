# Native CI

`.github/workflows/native.yml` runs on pull requests to `native` and pushes to `native`
(only when `native/` or the workflow changes).

| Job | What it does |
|---|---|
| toolchain | Qt 6.12.1 with aqtinstall (`install-qt.sh`), LayerShellQt built from pinned source (`build-layershell.sh`: Ubuntu has no usable LayerShellQt and its extra-cmake-modules is too old). Cached. |
| lint | `check_tokens.py` (no new hard-coded colours) and `qmllint.sh` (errors fail, warnings are reported). |
| rust | `cargo test --workspace` in `native/`. |
| shell | Builds `native/shell`, runs its ctest tests offscreen. |
| apps | Builds `native/qt`, `native/apps`, `native/apps/activity`, `native/apps/settings`; runs their ctest tests offscreen; renders screenshots of every app (`shots.sh`) and uploads them as the `native-screenshots` artifact. Look at it before you ask for review and attach the relevant images to your PR. |

## Tokens gate

Colours come from the Style/Look tokens. `check_tokens.py` counts colour literals per QML file and fails when a
file has more than its entry in `tokens-baseline.json`. The baseline holds the violations that existed when the
gate was added (shell glass and avatar files); fixing some never fails, and lowers the number when you run
`native/ci/check_tokens.py --update`. Never raise a baseline to get a PR through; add a token instead.

## Documented skips

* `native/check.sh` fmt, clippy and the Rust 1.85 MSRV build (CI runs `cargo test` on stable only).
* The sway based end to end scripts in `native/shell/tests/e2e-*.sh` and `native/compositor/` (they need a headless
  sway and grim, a different image). Run them on a desktop host.
