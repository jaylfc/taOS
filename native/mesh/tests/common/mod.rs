// SPDX-License-Identifier: AGPL-3.0-or-later
#![allow(dead_code)]
use serde_json::Value;
use std::path::{Path, PathBuf};
use taos_mesh::model::from_hex;

pub fn fixture(name: &str) -> Value {
    let p = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(name);
    serde_json::from_str(&std::fs::read_to_string(p).unwrap()).unwrap()
}

pub fn hex(v: &Value) -> Vec<u8> {
    from_hex(v.as_str().unwrap()).unwrap()
}

pub fn frame_named(fx: &Value, name: &str) -> Vec<u8> {
    let f = fx["frames"]
        .as_array()
        .unwrap()
        .iter()
        .find(|f| f["name"] == name)
        .unwrap_or_else(|| panic!("no fixture {name}"));
    hex(&f["hex"])
}

pub struct TmpDir(pub PathBuf);

impl TmpDir {
    pub fn new(tag: &str) -> Self {
        let n = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let p =
            std::env::temp_dir().join(format!("taos-mesh-test-{tag}-{}-{n}", std::process::id()));
        std::fs::create_dir_all(&p).unwrap();
        TmpDir(p)
    }
    pub fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for TmpDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}
