// SPDX-License-Identifier: AGPL-3.0-or-later
//! Argument parsing (hand written: the CLI is tiny and has no dependencies).

#[derive(Debug, Clone, PartialEq)]
pub struct StartArgs {
    pub to: String,
    pub size: String,
    pub fps: u32,
    pub tvpipe: String,
    pub no_input: bool,
    pub lock_cmd: Option<String>,
    pub unlock_cmd: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Cmd {
    Start(StartArgs),
    Stop,
    Status,
    Restore,
    Pair,
    Supervise(StartArgs),
    Watchdog,
    Help,
}

#[derive(Debug, PartialEq)]
pub struct Cli {
    pub json: bool,
    pub cmd: Cmd,
}

pub const USAGE: &str = "usage: taos-cast [--json] start --to <receiver> [--size WxH] [--fps N] [--tvpipe PATH] [--no-input] [--lock-cmd CMD] [--unlock-cmd CMD]\n       taos-cast [--json] stop | status | restore | pair";

pub fn valid_size(s: &str) -> bool {
    match s.split_once('x') {
        Some((w, h)) => [w, h].iter().all(|n| {
            !n.is_empty()
                && n.len() <= 4
                && n.bytes().all(|b| b.is_ascii_digit())
                && n.parse::<u32>().is_ok_and(|v| (16..=8192).contains(&v))
        }),
        None => false,
    }
}

pub fn parse(args: &[String]) -> Result<Cli, String> {
    let mut json = false;
    let mut rest: Vec<&str> = vec![];
    for a in args {
        if a == "--json" {
            json = true;
        } else {
            rest.push(a);
        }
    }
    let Some((&sub, flags)) = rest.split_first() else {
        return Ok(Cli {
            json,
            cmd: Cmd::Help,
        });
    };
    let cmd = match sub {
        "start" | "_supervise" => {
            let mut a = StartArgs {
                to: String::new(),
                size: "1920x1080".into(),
                fps: 30,
                tvpipe: "tvpipe".into(),
                no_input: false,
                lock_cmd: None,
                unlock_cmd: None,
            };
            let mut it = flags.iter();
            while let Some(&f) = it.next() {
                let mut val = |name: &str| -> Result<String, String> {
                    it.next()
                        .map(|s| s.to_string())
                        .ok_or(format!("{name} needs a value"))
                };
                match f {
                    "--to" => a.to = val("--to")?,
                    "--size" => a.size = val("--size")?,
                    "--fps" => {
                        a.fps = val("--fps")?
                            .parse()
                            .map_err(|_| "--fps must be a number")?
                    }
                    "--tvpipe" => a.tvpipe = val("--tvpipe")?,
                    "--lock-cmd" => a.lock_cmd = Some(val("--lock-cmd")?),
                    "--unlock-cmd" => a.unlock_cmd = Some(val("--unlock-cmd")?),
                    "--no-input" => a.no_input = true,
                    "--pair-url-from-engine" => {}
                    other => return Err(format!("unknown option {other}")),
                }
            }
            if a.to.trim().is_empty() || a.to.starts_with('-') {
                return Err("--to <receiver> is required".into());
            }
            if !valid_size(&a.size) {
                return Err("--size must look like 1920x1080".into());
            }
            if !(1..=120).contains(&a.fps) {
                return Err("--fps must be between 1 and 120".into());
            }
            if sub == "start" {
                Cmd::Start(a)
            } else {
                Cmd::Supervise(a)
            }
        }
        "stop" => Cmd::Stop,
        "status" => Cmd::Status,
        "restore" => Cmd::Restore,
        "pair" => Cmd::Pair,
        "_watchdog" => Cmd::Watchdog,
        "-h" | "--help" | "help" => Cmd::Help,
        other => return Err(format!("unknown command {other}")),
    };
    Ok(Cli { json, cmd })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn p(s: &str) -> Result<Cli, String> {
        parse(&s.split_whitespace().map(String::from).collect::<Vec<_>>())
    }

    #[test]
    fn start_defaults() {
        let c = p("start --to Bedroom").unwrap();
        let Cmd::Start(a) = c.cmd else { panic!() };
        assert_eq!(
            (a.size.as_str(), a.fps, a.no_input),
            ("1920x1080", 30, false)
        );
        assert!(!c.json);
    }

    #[test]
    fn json_anywhere_and_options() {
        let c = p("start --to TV --json --size 1280x720 --fps 60 --no-input").unwrap();
        assert!(c.json);
        let Cmd::Start(a) = c.cmd else { panic!() };
        assert_eq!((a.size.as_str(), a.fps, a.no_input), ("1280x720", 60, true));
    }

    #[test]
    fn refuses_bad_input() {
        assert!(p("start").is_err());
        assert!(p("start --to --size 1x1").is_err());
        assert!(p("start --to TV --size 0x0").is_err());
        assert!(p("start --to TV --size 1920").is_err());
        assert!(p("start --to TV --fps 0").is_err());
        assert!(p("start --to TV --bogus").is_err());
        assert!(p("frobnicate").is_err());
    }

    #[test]
    fn simple_commands() {
        assert_eq!(p("stop").unwrap().cmd, Cmd::Stop);
        assert_eq!(p("--json status").unwrap().cmd, Cmd::Status);
        assert_eq!(p("restore").unwrap().cmd, Cmd::Restore);
        assert_eq!(p("").unwrap().cmd, Cmd::Help);
    }
}
