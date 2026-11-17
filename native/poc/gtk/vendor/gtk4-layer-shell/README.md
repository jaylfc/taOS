[![Crate](https://img.shields.io/crates/v/gtk4-layer-shell.svg)](https://crates.io/crates/gtk4-layer-shell)
[![docs.rs](https://docs.rs/gtk4-layer-shell/badge.svg)](https://docs.rs/gtk4-layer-shell)
[![dependency status](https://deps.rs/crate/gtk4-layer-shell/0.8.1/status.svg)](https://deps.rs/crate/gtk4-layer-shell/0.8.1)

[![maintenance-status: passively-maintained (as of 2022-10-01)](https://img.shields.io/badge/maintenance--status-passively--maintained_%28as_of_2022--10--01%29-forestgreen)](https://gist.github.com/rusty-snake/574a91f1df9f97ec77ca308d6d731e29)
![dependabot status](https://img.shields.io/badge/dependabot-enabled-025e8c?logo=Dependabot)
[![Build](https://img.shields.io/github/actions/workflow/status/pentamassiv/gtk4-layer-shell-gir/build.yaml?branch=main)](https://github.com/pentamassiv/gtk4-layer-shell-gir/actions/workflows/build.yaml)

# gtk4-layer-shell
This crate allows building desktop shell components such as panels, notifications and wallpapers with GTK4. It can be used to anchor your windows to a corner or edge of the output, or stretch them across the entire output. To do that, it uses the [Layer Shell](https://wayland.app/protocols/wlr-layer-shell-unstable-v1) Wayland protocol. A list of supported compositors can be found [here](https://wayland.app/protocols/wlr-layer-shell-unstable-v1#compositor-support).

## Dependencies
You need to have `gtk4` and `gtk4-layer-shell` (the C library) installed on your system. `gtk4-layer-shell` version 1.1.0 or higher is needed to use `gtk4-session-lock`. If you want to use [gtk4-layer-shell](https://github.com/pentamassiv/gtk4-layer-shell-gir/tree/main/gtk4-layer-shell) and [gtk4-session-lock](https://github.com/pentamassiv/gtk4-layer-shell-gir/tree/main/gtk4-session-lock) together in a project, make sure to use the same .so file of `gtk4-layer-shell` for both.

If your distribution does not provide a current enough version of `gtk4-layer-shell`, you can [build it from source](https://github.com/wmww/gtk4-layer-shell#building-from-source). If you did that, you might also have to set the following two environment variables:
```bash
export PKG_CONFIG_PATH=/usr/local/lib/pkgconfig
export LD_LIBRARY_PATH=/usr/local/lib
```

## Usage
Have a look at the [simple example](https://github.com/pentamassiv/gtk4-layer-shell-gir/tree/main/gtk4-layer-shell/examples/simple-example.rs) to see how the bindings can be used. It works analogous to the original.

## Generating the wrapper
Generating the bindings yourself is not necessary to be able to use them. You can just use the version published on crates.io. If you want to do it anyways, you can find a description [here](https://github.com/pentamassiv/gtk4-layer-shell-gir/tree/main/generate_bindings.md).

## Maintenance status
This crate is just a safe wrapper for the C library so the bindings are feature complete and not actively worked on. The C library is actively developed and I keep the bindings up-to-date with it. If you encounter any problems, feel free to open a PR.

## Contributing
Pull requests are very welcome but please keep the maintenance status in mind.

## License
[MIT](https://choosealicense.com/licenses/mit/)
