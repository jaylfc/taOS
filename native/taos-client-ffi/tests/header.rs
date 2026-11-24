// SPDX-License-Identifier: AGPL-3.0-or-later
//! The hand-written C header must declare every exported symbol and carry the
//! same constants as the Rust side.

use std::process::Command;

const LIB: &str = include_str!("../src/lib.rs");
const HEADER: &str = include_str!("../include/taos_client.h");

#[test]
fn every_no_mangle_function_is_declared() {
    let mut n = 0;
    let mut lines = LIB.lines().peekable();
    while let Some(l) = lines.next() {
        if l.trim() == "#[no_mangle]" {
            let sig = lines.next().expect("fn after no_mangle");
            let name = sig
                .split("fn ")
                .nth(1)
                .and_then(|r| r.split('(').next())
                .expect("fn name");
            assert!(
                HEADER.contains(&format!("{name}(")),
                "{name} missing from taos_client.h"
            );
            n += 1;
        }
    }
    assert!(n >= 10, "found only {n} exported functions");
}

#[test]
fn constants_match() {
    for l in LIB.lines() {
        let l = l.trim();
        if let Some(rest) = l.strip_prefix("pub const TAOS_") {
            let (name, tail) = rest.split_once(':').unwrap();
            let value = tail.split('=').nth(1).unwrap().trim().trim_end_matches(';');
            let want = format!("#define TAOS_{name} {value}");
            let found = HEADER.lines().any(|h| {
                h.starts_with(&want)
                    && h[want.len()..]
                        .chars()
                        .next()
                        .is_none_or(char::is_whitespace)
            });
            assert!(found, "header lacks `{want}`");
        }
    }
}

#[test]
fn header_compiles_as_c_and_cpp() {
    let dir = std::env::temp_dir().join(format!("taos-hdr-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let inc = concat!(env!("CARGO_MANIFEST_DIR"), "/include");
    for (cc, ext) in [("cc", "c"), ("c++", "cpp")] {
        let f = dir.join(format!("t.{ext}"));
        std::fs::write(
            &f,
            "#include \"taos_client.h\"\nint main(void){return TAOS_OK;}\n",
        )
        .unwrap();
        let st = Command::new(cc)
            .args(["-fsyntax-only", "-Wall", "-Werror", "-I", inc])
            .arg(&f)
            .status();
        match st {
            Ok(s) => assert!(s.success(), "{cc} rejected the header"),
            Err(e) => panic!("{cc} is required to check the header: {e}"),
        }
    }
}
