use itertools::Itertools;
use std::{
    cell::Cell,
    collections::HashMap,
    env,
    ffi::OsString,
    path::{Path, PathBuf},
    rc::Rc,
    sync::{Mutex, OnceLock},
};

use assert_matches::assert_matches;

use crate::Dependencies;

use super::{
    BuildFlags, BuildInternalClosureError, Config, EnvVariables, Error, InternalLib, Library,
    Source,
};

pub(crate) static LOCK: OnceLock<Mutex<()>> = OnceLock::new();

fn tests_dir() -> PathBuf {
    env::current_dir().unwrap().join("src").join("tests")
}

pub(crate) struct EnvGuard(Vec<(&'static str, Option<OsString>)>);

impl EnvGuard {
    pub(crate) fn new(vars: &[&'static str]) -> Self {
        Self(vars.iter().map(|&var| (var, env::var_os(var))).collect())
    }
}

impl Drop for EnvGuard {
    fn drop(&mut self) {
        for (var, value) in &self.0 {
            match value {
                Some(value) => env::set_var(var, value),
                None => env::remove_var(var),
            }
        }
    }
}

fn create_config(path: &str, env: Vec<(&'static str, &'static str)>) -> Config {
    // PKG_CONFIG_PATH is read by pkg-config, so we need to actually change the env.
    // Callers must hold LOCK for the entire duration of the test to avoid races.
    env::set_var("PKG_CONFIG_PATH", tests_dir());
    create_config_impl(path, env, None)
}

// NOTE: You must ensure you call env::remove_var on `TARGET` and `PKG_CONFIG_ALLOW_CROSS` after you
// invoke pkg-config if `target` is `Some`!
fn create_config_impl(
    path: &str,
    env: Vec<(&'static str, &'static str)>,
    target: Option<&str>,
) -> Config {
    // PKG_CONFIG_PATH is read by pkg-config, so we need to actually change the env.
    // Callers must hold LOCK for the entire duration of the test to avoid races.
    env::set_var("PKG_CONFIG_PATH", tests_dir());

    // TARGET and PKG_CONFIG_ALLOW_CROSS are also read by pkg-config, so we need to actually
    // change the env
    if let Some(target) = target {
        env::set_var("TARGET", target);
        env::set_var("PKG_CONFIG_ALLOW_CROSS", "true");
    }

    let mut hash = HashMap::new();
    hash.insert(
        "CARGO_MANIFEST_DIR",
        tests_dir().join(path).to_string_lossy().to_string(),
    );

    hash.insert("CARGO_FEATURE_TEST_FEATURE", "".to_string());
    env.iter().for_each(|(k, v)| {
        hash.insert(k, v.to_string());
    });

    #[cfg(feature = "binary")]
    hash.insert("SYSTEM_DEPS_NO_PREBUILT", "".to_string());

    Config::new_with_env(EnvVariables::Mock(hash))
}

fn toml(
    path: &str,
    env: Vec<(&'static str, &'static str)>,
) -> Result<(Dependencies, BuildFlags), Error> {
    // Hold the lock for the entire probe since it depends on PKG_CONFIG_PATH
    let _l = LOCK.get_or_init(|| Mutex::new(())).lock();
    let libs = create_config(path, env).probe_full()?;
    let flags = libs.gen_flags(None)?;
    Ok((libs, flags))
}

fn toml_with_target(
    path: &str,
    env: Vec<(&'static str, &'static str)>,
    target: &str,
) -> Result<(Dependencies, BuildFlags), Error> {
    let _l = LOCK.get_or_init(|| Mutex::new(())).lock();
    let libs = create_config_impl(path, env, Some(target)).probe_full()?;

    env::remove_var("TARGET");
    env::remove_var("PKG_CONFIG_ALLOW_CROSS");

    let flags = libs.gen_flags(Some(target))?;

    Ok((libs, flags))
}

fn assert_flags(flags: BuildFlags, expected: &str) {
    // flags ordering isn't guaranteed so sort them out before comparing
    let flags = flags.to_string().split('\n').sorted().join("\n");
    let expected = expected.split('\n').sorted().join("\n");
    assert_eq!(flags, expected);
}

#[test]
fn good() {
    let (libraries, flags) = toml("toml-good", vec![]).unwrap();
    let testlib = libraries.get_by_name("testlib").unwrap();
    assert_eq!(testlib.version, "1.2.3");
    assert_eq!(
        testlib.defines.get("BADGER").unwrap().as_deref(),
        Some("yes")
    );
    assert!(testlib.defines.get("AWESOME").unwrap().is_none());

    let testdata = libraries.get_by_name("testdata").unwrap();
    assert_eq!(testdata.version, "4.5.6");
    assert!(libraries.get_by_name("testmore").is_none());

    assert_eq!(libraries.iter().len(), 2);

    assert_flags(
        flags,
        r#"cargo:rustc-link-search=native=/usr/lib/
cargo:rustc-link-search=framework=/usr/lib/
cargo:rustc-link-lib=test
cargo:rustc-link-lib=framework=someframework
cargo:include=/usr/include/testlib
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_INCLUDE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LDFLAGS
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LIB
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LIB_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_NO_PKG_CONFIG
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_SEARCH_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_SEARCH_NATIVE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_INCLUDE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LDFLAGS
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LIB
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LIB_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_NO_PKG_CONFIG
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_SEARCH_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_SEARCH_NATIVE
cargo:rerun-if-env-changed=SYSTEM_DEPS_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_NO_PREBUILT
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_NO_PREBUILT
cargo:rerun-if-env-changed=SYSTEM_DEPS_LINK
"#,
    );
}

#[test]
fn version_range() {
    let (libraries, _flags) = toml("toml-version-range", vec![]).unwrap();
    let testlib = libraries.get_by_name("testlib").unwrap();
    assert_eq!(testlib.version, "1.2.3");
    assert_eq!(
        testlib.defines.get("BADGER").unwrap().as_deref(),
        Some("yes")
    );
    assert!(testlib.defines.get("AWESOME").unwrap().is_none());

    let testdata = libraries.get_by_name("testdata").unwrap();
    assert_eq!(testdata.version, "4.5.6");

    assert_eq!(libraries.iter().len(), 2);
}

#[test]
#[ignore]
fn version_range_unsatisfied() {
    let err = toml_err("toml-version-range-unsatisfied");

    assert_matches!(err, Error::PkgConfig(_));

    let err_msg = err.to_string();
    // pkgconf and pkg-config give different error messages
    if !err_msg.contains("Package 'testlib' has version '1.2.3', required version is '< 1.2'")
        && !err_msg.contains("Requested 'testlib < 1.2' but version of Test Library is 1.2.3")
    {
        panic!("Error not as expected: {:?}", err);
    }
}

fn toml_err(path: &str) -> Error {
    toml(path, vec![]).unwrap_err()
}

fn toml_err_invalid(path: &str, err_ends_with: &str) {
    let err = toml_err(path);
    assert_matches!(err, Error::InvalidMetadata(_));

    if !err.to_string().ends_with(err_ends_with) {
        panic!(
            "Expected error to end with: {:?}\nGot error: {:?}",
            err_ends_with, err
        );
    }
}

// Assert a PkgConfig error because requested lib version cannot be found
fn toml_pkg_config_err_version(
    path: &str,
    expected_version: &str,
    env_vars: Vec<(&'static str, &'static str)>,
) {
    let err = toml(path, env_vars).unwrap_err();
    match err {
        Error::PkgConfig(e) => match e {
            pkg_config::Error::ProbeFailure {
                command: cmd,
                output: _,
                name: _,
            } => {
                let s = format!(">= {expected_version}");
                // Remove trailing quotes, if any
                let cmd = cmd.trim_end_matches(['"', '\'']);
                assert!(cmd.ends_with(&s));
            }
            _ => panic!("Wrong pkg-config error type"),
        },
        _ => panic!("Wrong error type"),
    }
}

#[test]
fn missing_file() {
    assert_matches!(toml_err("toml-missing-file"), Error::FailToRead(_, _));
}

#[test]
fn missing_key() {
    toml_err_invalid(
        "toml-missing-key",
        "missing key `package.metadata.system-deps`",
    );
}

#[test]
fn not_table() {
    toml_err_invalid(
        "toml-not-table",
        "`package.metadata.system-deps` is not a table",
    );
}

#[test]
fn version_missing() {
    toml_err_invalid("toml-version-missing", "No version defined for testlib");
}

#[test]
fn version_not_string() {
    toml_err_invalid(
        "toml-version-not-string",
        "`package.metadata.system-deps.testlib`: not a string or a table",
    );
}

#[test]
fn version_in_table_not_string() {
    toml_err_invalid(
        "toml-version-in-table-not-string",
        "metadata.system-deps.testlib: unexpected key version type integer",
    );
}

#[test]
fn feature_not_string() {
    toml_err_invalid(
        "toml-feature-not-string",
        "metadata.system-deps.testlib: unexpected key feature type integer",
    );
}

#[test]
fn unexpected_key() {
    toml_err_invalid(
        "toml-unexpected-key",
        "metadata.system-deps.testlib: unexpected key color type string",
    );
}

#[test]
fn override_name() {
    let (libraries, _) = toml("toml-override-name", vec![]).unwrap();
    let testlib = libraries.get_by_name("test_lib").unwrap();
    assert_eq!(testlib.name, "testlib");
    assert_eq!(testlib.version, "1.2.3");

    // Enable feature 1.2
    let (libraries, _) = toml("toml-override-name", vec![("CARGO_FEATURE_V1_2", "")]).unwrap();
    let testlib = libraries.get_by_name("test_lib").unwrap();
    assert_eq!(testlib.name, "testlib");
    assert_eq!(testlib.version, "1.2.3");
}

#[test]
fn fallback_names() {
    let (libraries, flags) = toml("toml-fallback-names", vec![]).unwrap();
    let testlib = libraries.get_by_name("test_lib").unwrap();
    assert_eq!(testlib.name, "testlib");
    assert_eq!(testlib.version, "1.2.3");

    eprintln!();
    eprintln!("{flags}");
    assert_flags(
        flags,
        r#"cargo:rustc-link-search=native=/usr/lib/
cargo:rustc-link-search=framework=/usr/lib/
cargo:rustc-link-lib=test
cargo:rustc-link-lib=framework=someframework
cargo:include=/usr/include/testlib
cargo:rerun-if-env-changed=SYSTEM_DEPS_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TEST_LIB_LIB
cargo:rerun-if-env-changed=SYSTEM_DEPS_TEST_LIB_LIB_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TEST_LIB_SEARCH_NATIVE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TEST_LIB_SEARCH_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TEST_LIB_INCLUDE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TEST_LIB_LDFLAGS
cargo:rerun-if-env-changed=SYSTEM_DEPS_TEST_LIB_NO_PKG_CONFIG
cargo:rerun-if-env-changed=SYSTEM_DEPS_TEST_LIB_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TEST_LIB_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TEST_LIB_NO_PREBUILT
"#,
    );
}

#[test]
fn version_fallback_names() {
    let (libraries, flags) = toml("toml-version-fallback-names", vec![]).unwrap();
    let testlib = libraries.get_by_name("test_lib").unwrap();
    assert_eq!(testlib.name, "testlib");
    assert_eq!(testlib.version, "1.2.3");
    assert_flags(
        flags,
        r#"cargo:rustc-link-search=native=/usr/lib/
cargo:rustc-link-search=framework=/usr/lib/
cargo:rustc-link-lib=test
cargo:rustc-link-lib=framework=someframework
cargo:include=/usr/include/testlib
cargo:rerun-if-env-changed=SYSTEM_DEPS_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TEST_LIB_LIB
cargo:rerun-if-env-changed=SYSTEM_DEPS_TEST_LIB_LIB_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TEST_LIB_SEARCH_NATIVE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TEST_LIB_SEARCH_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TEST_LIB_INCLUDE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TEST_LIB_LDFLAGS
cargo:rerun-if-env-changed=SYSTEM_DEPS_TEST_LIB_NO_PKG_CONFIG
cargo:rerun-if-env-changed=SYSTEM_DEPS_TEST_LIB_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TEST_LIB_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TEST_LIB_NO_PREBUILT
"#,
    );

    // Now try with v2 feature enabled.
    let (libraries, flags) = toml(
        "toml-version-fallback-names",
        vec![("CARGO_FEATURE_V2", "")],
    )
    .unwrap();
    let testlib = libraries.get_by_name("test_lib").unwrap();
    assert_eq!(testlib.name, "testlib-2.0");
    assert_eq!(testlib.version, "2.0.0");

    assert_flags(
        flags,
        r#"cargo:rustc-link-search=native=/usr/lib/
cargo:rustc-link-lib=test
cargo:include=/usr/include/testlib
cargo:rerun-if-env-changed=SYSTEM_DEPS_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TEST_LIB_LIB
cargo:rerun-if-env-changed=SYSTEM_DEPS_TEST_LIB_LIB_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TEST_LIB_SEARCH_NATIVE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TEST_LIB_SEARCH_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TEST_LIB_INCLUDE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TEST_LIB_LDFLAGS
cargo:rerun-if-env-changed=SYSTEM_DEPS_TEST_LIB_NO_PKG_CONFIG
cargo:rerun-if-env-changed=SYSTEM_DEPS_TEST_LIB_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TEST_LIB_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TEST_LIB_NO_PREBUILT
"#,
    );
}

#[test]
fn feature_versions() {
    let (libraries, _) = toml("toml-feature-versions", vec![]).unwrap();
    let testdata = libraries.get_by_name("testdata").unwrap();
    assert_eq!(testdata.name, "testdata");
    assert_eq!(testdata.version, "4.5.6");

    // version 5 is not available
    toml_pkg_config_err_version("toml-feature-versions", "5", vec![("CARGO_FEATURE_V5", "")]);

    // We check the highest version enabled by features
    toml_pkg_config_err_version("toml-feature-versions", "6", vec![("CARGO_FEATURE_V6", "")]);

    let (libraries, _) = toml("toml-version-names", vec![]).unwrap();
    let testlib = libraries.get_by_name("testlib").unwrap();
    assert_eq!(testlib.name, "testlib");
    assert_eq!(testlib.version, "1.2.3");

    // Enable feature v2
    let (libraries, _) = toml("toml-version-names", vec![("CARGO_FEATURE_V2", "")]).unwrap();
    let testlib = libraries.get_by_name("testlib").unwrap();
    assert_eq!(testlib.name, "testlib-2.0");
    assert_eq!(testlib.version, "2.0.0");

    // Takes the higher feature
    let (libraries, _) = toml(
        "toml-version-names",
        vec![("CARGO_FEATURE_V2", ""), ("CARGO_FEATURE_V3", "")],
    )
    .unwrap();
    let testlib = libraries.get_by_name("testlib").unwrap();
    assert_eq!(testlib.name, "testlib-3.0");
}

#[test]
fn override_search_native() {
    #[cfg(target_os = "windows")]
    let paths_env = "/custom/path;/other/path";
    #[cfg(not(target_os = "windows"))]
    let paths_env = "/custom/path:/other/path";

    let (libraries, flags) = toml(
        "toml-good",
        vec![("SYSTEM_DEPS_TESTLIB_SEARCH_NATIVE", paths_env)],
    )
    .unwrap();
    let testlib = libraries.get_by_name("testlib").unwrap();
    assert_eq!(
        testlib.link_paths,
        vec![Path::new("/custom/path"), Path::new("/other/path")]
    );

    assert_flags(
        flags,
        r#"cargo:rustc-link-search=native=/custom/path
cargo:rustc-link-search=native=/other/path
cargo:rustc-link-search=framework=/usr/lib/
cargo:rustc-link-lib=test
cargo:rustc-link-lib=framework=someframework
cargo:include=/usr/include/testlib
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_INCLUDE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LDFLAGS
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LIB
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LIB_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_NO_PKG_CONFIG
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_SEARCH_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_SEARCH_NATIVE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_INCLUDE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LDFLAGS
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LIB
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LIB_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_NO_PKG_CONFIG
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_SEARCH_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_SEARCH_NATIVE
cargo:rerun-if-env-changed=SYSTEM_DEPS_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_NO_PREBUILT
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_NO_PREBUILT
cargo:rerun-if-env-changed=SYSTEM_DEPS_LINK
"#,
    );
}

#[test]
fn override_search_framework() {
    let (libraries, flags) = toml(
        "toml-good",
        vec![("SYSTEM_DEPS_TESTLIB_SEARCH_FRAMEWORK", "/custom/path")],
    )
    .unwrap();
    let testlib = libraries.get_by_name("testlib").unwrap();
    assert_eq!(testlib.framework_paths, vec![Path::new("/custom/path")]);

    assert_flags(
        flags,
        r#"cargo:rustc-link-search=native=/usr/lib/
cargo:rustc-link-search=framework=/custom/path
cargo:rustc-link-lib=test
cargo:rustc-link-lib=framework=someframework
cargo:include=/usr/include/testlib
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_INCLUDE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LDFLAGS
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LIB
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LIB_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_NO_PKG_CONFIG
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_SEARCH_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_SEARCH_NATIVE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_INCLUDE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LDFLAGS
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LIB
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LIB_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_NO_PKG_CONFIG
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_SEARCH_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_SEARCH_NATIVE
cargo:rerun-if-env-changed=SYSTEM_DEPS_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_NO_PREBUILT
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_NO_PREBUILT
cargo:rerun-if-env-changed=SYSTEM_DEPS_LINK
"#,
    );
}

#[test]
fn override_lib() {
    let (libraries, flags) = toml(
        "toml-good",
        vec![("SYSTEM_DEPS_TESTLIB_LIB", "overridden-test other-test")],
    )
    .unwrap();
    let testlib = libraries.get_by_name("testlib").unwrap();
    assert_eq!(
        testlib.libs,
        vec!["overridden-test", "other-test"]
            .into_iter()
            .map(|name| InternalLib::new(name.to_string(), false))
            .collect::<Vec<InternalLib>>()
    );

    assert_flags(
        flags,
        r#"cargo:rustc-link-search=native=/usr/lib/
cargo:rustc-link-search=framework=/usr/lib/
cargo:rustc-link-lib=overridden-test
cargo:rustc-link-lib=other-test
cargo:rustc-link-lib=framework=someframework
cargo:include=/usr/include/testlib
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_INCLUDE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LDFLAGS
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LIB
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LIB_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_NO_PKG_CONFIG
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_SEARCH_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_SEARCH_NATIVE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_INCLUDE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LDFLAGS
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LIB
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LIB_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_NO_PKG_CONFIG
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_SEARCH_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_SEARCH_NATIVE
cargo:rerun-if-env-changed=SYSTEM_DEPS_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_NO_PREBUILT
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_NO_PREBUILT
cargo:rerun-if-env-changed=SYSTEM_DEPS_LINK
"#,
    );
}

#[test]
fn override_framework() {
    let (libraries, flags) = toml(
        "toml-good",
        vec![("SYSTEM_DEPS_TESTLIB_LIB_FRAMEWORK", "overridden-framework")],
    )
    .unwrap();
    let testlib = libraries.get_by_name("testlib").unwrap();
    assert_eq!(testlib.frameworks, vec!["overridden-framework"]);

    assert_flags(
        flags,
        r#"cargo:rustc-link-search=native=/usr/lib/
cargo:rustc-link-search=framework=/usr/lib/
cargo:rustc-link-lib=test
cargo:rustc-link-lib=framework=overridden-framework
cargo:include=/usr/include/testlib
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_INCLUDE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LDFLAGS
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LIB
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LIB_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_NO_PKG_CONFIG
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_SEARCH_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_SEARCH_NATIVE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_INCLUDE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LDFLAGS
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LIB
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LIB_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_NO_PKG_CONFIG
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_SEARCH_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_SEARCH_NATIVE
cargo:rerun-if-env-changed=SYSTEM_DEPS_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_NO_PREBUILT
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_NO_PREBUILT
cargo:rerun-if-env-changed=SYSTEM_DEPS_LINK
"#,
    );
}

#[test]
fn override_include() {
    let (libraries, flags) = toml(
        "toml-good",
        vec![("SYSTEM_DEPS_TESTLIB_INCLUDE", "/other/include")],
    )
    .unwrap();
    let testlib = libraries.get_by_name("testlib").unwrap();
    assert_eq!(testlib.include_paths, vec![Path::new("/other/include")]);

    assert_flags(
        flags,
        r#"cargo:rustc-link-search=native=/usr/lib/
cargo:rustc-link-search=framework=/usr/lib/
cargo:rustc-link-lib=test
cargo:rustc-link-lib=framework=someframework
cargo:include=/other/include
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_INCLUDE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LDFLAGS
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LIB
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LIB_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_NO_PKG_CONFIG
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_SEARCH_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_SEARCH_NATIVE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_INCLUDE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LDFLAGS
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LIB
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LIB_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_NO_PKG_CONFIG
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_SEARCH_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_SEARCH_NATIVE
cargo:rerun-if-env-changed=SYSTEM_DEPS_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_NO_PREBUILT
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_NO_PREBUILT
cargo:rerun-if-env-changed=SYSTEM_DEPS_LINK
"#,
    );
}

#[test]
fn override_unset() {
    let (libraries, flags) = toml(
        "toml-good",
        vec![
            ("SYSTEM_DEPS_TESTLIB_SEARCH_NATIVE", ""),
            ("SYSTEM_DEPS_TESTLIB_SEARCH_FRAMEWORK", ""),
            ("SYSTEM_DEPS_TESTLIB_LIB", ""),
            ("SYSTEM_DEPS_TESTLIB_LIB_FRAMEWORK", ""),
            ("SYSTEM_DEPS_TESTLIB_INCLUDE", ""),
        ],
    )
    .unwrap();
    let testlib = libraries.get_by_name("testlib").unwrap();
    assert_eq!(testlib.link_paths, Vec::<PathBuf>::new());
    assert_eq!(testlib.framework_paths, Vec::<PathBuf>::new());
    assert_eq!(testlib.libs, Vec::<InternalLib>::new());
    assert_eq!(testlib.frameworks, Vec::<String>::new());
    assert_eq!(testlib.include_paths, Vec::<PathBuf>::new());

    assert_flags(
        flags,
        r"cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_INCLUDE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LDFLAGS
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LIB
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LIB_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_NO_PKG_CONFIG
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_SEARCH_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_SEARCH_NATIVE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_INCLUDE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LDFLAGS
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LIB
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LIB_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_NO_PKG_CONFIG
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_SEARCH_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_SEARCH_NATIVE
cargo:rerun-if-env-changed=SYSTEM_DEPS_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_NO_PREBUILT
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_NO_PREBUILT
cargo:rerun-if-env-changed=SYSTEM_DEPS_LINK
",
    );
}

#[test]
fn override_no_pkg_config() {
    let (libraries, flags) = toml(
        "toml-good",
        vec![
            ("SYSTEM_DEPS_TESTLIB_NO_PKG_CONFIG", "1"),
            ("SYSTEM_DEPS_TESTLIB_LIB", "custom-lib"),
        ],
    )
    .unwrap();
    let testlib = libraries.get_by_name("testlib").unwrap();
    assert_eq!(testlib.link_paths, Vec::<PathBuf>::new());
    assert_eq!(testlib.framework_paths, Vec::<PathBuf>::new());
    assert_eq!(
        testlib.libs,
        vec![InternalLib::new("custom-lib".to_string(), false)]
    );
    assert_eq!(testlib.frameworks, Vec::<String>::new());
    assert_eq!(testlib.include_paths, Vec::<PathBuf>::new());

    assert_flags(
        flags,
        r"cargo:rustc-link-lib=custom-lib
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_INCLUDE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LDFLAGS
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LIB
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LIB_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_NO_PKG_CONFIG
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_SEARCH_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_SEARCH_NATIVE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_INCLUDE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LDFLAGS
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LIB
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LIB_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_NO_PKG_CONFIG
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_SEARCH_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_SEARCH_NATIVE
cargo:rerun-if-env-changed=SYSTEM_DEPS_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_NO_PREBUILT
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_NO_PREBUILT
cargo:rerun-if-env-changed=SYSTEM_DEPS_LINK
",
    );
}

#[test]
fn override_no_pkg_config_error() {
    let err = toml(
        "toml-good",
        vec![("SYSTEM_DEPS_TESTLIB_NO_PKG_CONFIG", "1")],
    )
    .unwrap_err();
    assert_eq!(
        err.to_string(),
        "You should define at least one lib using SYSTEM_DEPS_TESTLIB_LIB or SYSTEM_DEPS_TESTLIB_LIB_FRAMEWORK"
    );
}

fn test_build_internal(
    path: &'static str,
    env: Vec<(&'static str, &'static str)>,
    expected_lib: &'static str,
) -> Result<(Dependencies, bool), (Error, bool)> {
    let _l = LOCK.get_or_init(|| Mutex::new(())).lock();
    let called = Rc::new(Cell::new(false));
    let called_clone = called.clone();
    let config = create_config(path, env).add_build_internal(expected_lib, move |lib, version| {
        called_clone.replace(true);
        assert_eq!(lib, expected_lib);
        let mut pkg_lib = pkg_config::Config::new()
            .print_system_libs(false)
            .cargo_metadata(false)
            .probe(lib)
            .unwrap();
        pkg_lib.version = version.to_string();
        Ok(Library::from_pkg_config(lib, pkg_lib))
    });

    match config.probe_full() {
        Ok(libraries) => Ok((libraries, called.get())),
        Err(e) => Err((e, called.get())),
    }
}

#[test]
fn build_internal_always() {
    let (libraries, called) = test_build_internal(
        "toml-good",
        vec![("SYSTEM_DEPS_TESTLIB_BUILD_INTERNAL", "always")],
        "testlib",
    )
    .unwrap();

    assert!(called);
    assert!(libraries.get_by_name("testlib").is_some());
}

#[test]
fn build_internal_auto_not_called() {
    // No need to build the lib as the existing version is new enough
    let (libraries, called) = test_build_internal(
        "toml-good",
        vec![("SYSTEM_DEPS_TESTLIB_BUILD_INTERNAL", "auto")],
        "testlib",
    )
    .unwrap();

    assert!(!called);
    assert!(libraries.get_by_name("testlib").is_some());
}

#[test]
fn build_internal_auto_called() {
    // Version 5 is not available, so we should try building
    let (libraries, called) = test_build_internal(
        "toml-feature-versions",
        vec![
            ("SYSTEM_DEPS_TESTDATA_BUILD_INTERNAL", "auto"),
            ("CARGO_FEATURE_V5", ""),
        ],
        "testdata",
    )
    .unwrap();

    assert!(called);
    assert!(libraries.get_by_name("testdata").is_some());
}

#[test]
fn build_internal_auto_never() {
    // Version 5 is not available, but we forbid to build the lib
    let (err, called) = test_build_internal(
        "toml-feature-versions",
        vec![
            ("SYSTEM_DEPS_TESTDATA_BUILD_INTERNAL", "never"),
            ("CARGO_FEATURE_V5", ""),
        ],
        "testdata",
    )
    .unwrap_err();

    assert!(matches!(err, Error::PkgConfig(..)));
    assert!(!called);
}

#[test]
fn build_internal_always_no_closure() {
    let _l = LOCK.get_or_init(|| Mutex::new(())).lock();
    let config = create_config(
        "toml-good",
        vec![("SYSTEM_DEPS_TESTLIB_BUILD_INTERNAL", "always")],
    );

    let err = config.probe_full().unwrap_err();
    assert!(matches!(err, Error::BuildInternalNoClosure(..)));
}

#[test]
fn build_internal_invalid() {
    let _l = LOCK.get_or_init(|| Mutex::new(())).lock();
    let config = create_config(
        "toml-good",
        vec![("SYSTEM_DEPS_TESTLIB_BUILD_INTERNAL", "badger")],
    );

    let err = config.probe_full().unwrap_err();
    assert!(matches!(err, Error::BuildInternalInvalid(..)));
}

#[test]
fn build_internal_wrong_version() {
    let _l = LOCK.get_or_init(|| Mutex::new(())).lock();
    // Require version 5
    let called = Rc::new(Cell::new(false));
    let called_clone = called.clone();
    let config = create_config(
        "toml-feature-versions",
        vec![
            ("SYSTEM_DEPS_TESTDATA_BUILD_INTERNAL", "auto"),
            ("CARGO_FEATURE_V5", ""),
        ],
    )
    .add_build_internal("testdata", move |lib, _version| {
        called_clone.replace(true);
        assert_eq!(lib, "testdata");
        let pkg_lib = pkg_config::Config::new()
            .print_system_libs(false)
            .cargo_metadata(false)
            .probe(lib)
            .unwrap();
        Ok(Library::from_pkg_config(lib, pkg_lib))
    });

    let err = config.probe_full().unwrap_err();
    assert!(matches!(err, Error::BuildInternalWrongVersion(..)));
    assert!(called.get());
}

#[test]
fn build_internal_fail() {
    let _l = LOCK.get_or_init(|| Mutex::new(())).lock();
    let called = Rc::new(Cell::new(false));
    let called_clone = called.clone();
    let config = create_config(
        "toml-good",
        vec![("SYSTEM_DEPS_TESTLIB_BUILD_INTERNAL", "always")],
    )
    .add_build_internal("testlib", move |lib, _version| {
        called_clone.replace(true);
        assert_eq!(lib, "testlib");
        Err(BuildInternalClosureError::failed("Something went wrong"))
    });

    let err = config.probe_full().unwrap_err();
    assert!(matches!(err, Error::BuildInternalClosureError(..)));
    assert!(called.get());
}

#[test]
fn build_internal_always_global() {
    let _l = LOCK.get_or_init(|| Mutex::new(())).lock();
    let called = Rc::new(Cell::new((false, false)));
    let called_clone = called.clone();
    let called_clone2 = called.clone();
    let config = create_config("toml-good", vec![("SYSTEM_DEPS_BUILD_INTERNAL", "always")])
        .add_build_internal("testlib", move |lib, version| {
            let (_, b) = called_clone.get();
            called_clone.replace((true, b));
            let mut pkg_lib = pkg_config::Config::new()
                .print_system_libs(false)
                .cargo_metadata(false)
                .probe(lib)
                .unwrap();
            pkg_lib.version = version.to_string();
            Ok(Library::from_pkg_config(lib, pkg_lib))
        })
        .add_build_internal("testdata", move |lib, version| {
            let (a, _) = called_clone2.get();
            called_clone2.replace((a, true));
            let mut pkg_lib = pkg_config::Config::new()
                .print_system_libs(false)
                .cargo_metadata(false)
                .probe(lib)
                .unwrap();
            pkg_lib.version = version.to_string();
            Ok(Library::from_pkg_config(lib, pkg_lib))
        });

    let libraries = config.probe_full().unwrap();
    assert_eq!(called.get(), (true, true));
    assert!(libraries.get_by_name("testlib").is_some());
    assert!(libraries.get_by_name("testdata").is_some());
}

#[test]
fn internal_pkg_config_any_version() {
    let _l = LOCK.get_or_init(|| Mutex::new(())).lock();
    let _env = EnvGuard::new(&["PKG_CONFIG_PATH", "PKG_CONFIG_PATH_FOR_TARGET"]);

    let lib = Library::from_internal_pkg_config(&tests_dir(), "teststaticrequires", "0").unwrap();
    let libs: Vec<_> = lib
        .libs
        .iter()
        .map(|l| (l.name.as_str(), l.is_static_available))
        .collect();
    let statik = !cfg!(all(target_os = "windows", target_env = "msvc"));
    assert_eq!(libs, [("testrequires", false), ("teststatic", statik)]);

    assert!(Library::from_internal_pkg_config(&tests_dir(), "teststaticrequires", "9").is_err());
}

#[cfg(not(all(target_os = "windows", target_env = "msvc")))]
#[test]
fn internal_pkg_config_static_in_any_dir() {
    let _l = LOCK.get_or_init(|| Mutex::new(())).lock();
    let _env = EnvGuard::new(&["PKG_CONFIG_PATH", "PKG_CONFIG_PATH_FOR_TARGET"]);

    let lib = Library::from_internal_pkg_config(&tests_dir(), "teststatictwodirs", "0").unwrap();
    let libs: Vec<_> = lib
        .libs
        .iter()
        .map(|l| (l.name.as_str(), l.is_static_available))
        .collect();
    assert_eq!(libs, [("teststatic", true), ("test", true)]);
}

#[test]
fn wrap_pkg_config_without_pkg_config_path() {
    let _l = LOCK.get_or_init(|| Mutex::new(())).lock();
    let _env = EnvGuard::new(&["PKG_CONFIG_PATH", "PKG_CONFIG_PATH_FOR_TARGET"]);
    env::remove_var("PKG_CONFIG_PATH");
    env::remove_var("PKG_CONFIG_PATH_FOR_TARGET");

    let pc_dir = tests_dir();
    Library::wrap_pkg_config(&pc_dir, || {
        let paths: Vec<_> = env::split_paths(&env::var_os("PKG_CONFIG_PATH").unwrap()).collect();
        assert_eq!(paths, std::slice::from_ref(&pc_dir));
        assert!(env::var_os("PKG_CONFIG_PATH_FOR_TARGET").is_none());
        Ok(())
    })
    .unwrap();

    assert!(env::var_os("PKG_CONFIG_PATH").is_none());
    assert!(env::var_os("PKG_CONFIG_PATH_FOR_TARGET").is_none());
}

#[test]
fn wrap_pkg_config_with_pkg_config_path() {
    let _l = LOCK.get_or_init(|| Mutex::new(())).lock();
    let _env = EnvGuard::new(&["PKG_CONFIG_PATH", "PKG_CONFIG_PATH_FOR_TARGET"]);
    let old = PathBuf::from("/usr/lib/fake/pkgconfig");
    env::set_var("PKG_CONFIG_PATH", &old);
    env::remove_var("PKG_CONFIG_PATH_FOR_TARGET");

    let pc_dir = tests_dir();
    Library::wrap_pkg_config(&pc_dir, || {
        let paths: Vec<_> = env::split_paths(&env::var_os("PKG_CONFIG_PATH").unwrap()).collect();
        assert_eq!(paths, [pc_dir.clone(), old.clone()]);
        assert!(env::var_os("PKG_CONFIG_PATH_FOR_TARGET").is_none());
        Ok(())
    })
    .unwrap();

    assert_eq!(env::var_os("PKG_CONFIG_PATH"), Some(old.into()));
    assert!(env::var_os("PKG_CONFIG_PATH_FOR_TARGET").is_none());
}

#[test]
fn wrap_pkg_config_with_only_pkg_config_path_for_target() {
    let _l = LOCK.get_or_init(|| Mutex::new(())).lock();
    let _env = EnvGuard::new(&["PKG_CONFIG_PATH", "PKG_CONFIG_PATH_FOR_TARGET"]);
    let nix = PathBuf::from("/nix/store/fake/lib/pkgconfig");
    env::remove_var("PKG_CONFIG_PATH");
    env::set_var("PKG_CONFIG_PATH_FOR_TARGET", &nix);

    let pc_dir = tests_dir();
    Library::wrap_pkg_config(&pc_dir, || {
        let get = |var| env::split_paths(&env::var_os(var).unwrap()).collect::<Vec<_>>();
        assert_eq!(get("PKG_CONFIG_PATH"), std::slice::from_ref(&pc_dir));
        assert_eq!(
            get("PKG_CONFIG_PATH_FOR_TARGET"),
            [pc_dir.clone(), nix.clone()]
        );
        Ok(())
    })
    .unwrap();

    assert!(env::var_os("PKG_CONFIG_PATH").is_none());
    assert_eq!(env::var_os("PKG_CONFIG_PATH_FOR_TARGET"), Some(nix.into()));
}

#[test]
fn wrap_pkg_config_without_paths_to_add() {
    let _l = LOCK.get_or_init(|| Mutex::new(())).lock();
    let _env = EnvGuard::new(&["PKG_CONFIG_PATH", "PKG_CONFIG_PATH_FOR_TARGET"]);
    env::remove_var("PKG_CONFIG_PATH");
    env::remove_var("PKG_CONFIG_PATH_FOR_TARGET");

    let check = || {
        assert!(env::var_os("PKG_CONFIG_PATH").is_none());
        assert!(env::var_os("PKG_CONFIG_PATH_FOR_TARGET").is_none());
        Ok(())
    };
    Library::wrap_pkg_config(None::<&Path>, check).unwrap();
    Library::wrap_pkg_config(Some(&Vec::<PathBuf>::new()), check).unwrap();
}

#[test]
fn wrap_pkg_config_restores_on_error() {
    let _l = LOCK.get_or_init(|| Mutex::new(())).lock();
    let _env = EnvGuard::new(&["PKG_CONFIG_PATH", "PKG_CONFIG_PATH_FOR_TARGET"]);
    env::remove_var("PKG_CONFIG_PATH");
    env::remove_var("PKG_CONFIG_PATH_FOR_TARGET");

    let res = Library::wrap_pkg_config(tests_dir(), || {
        Err::<(), _>(pkg_config::Error::EnvNoPkgConfig("X".into()))
    });

    assert!(res.is_err());
    assert!(env::var_os("PKG_CONFIG_PATH").is_none());
    assert!(env::var_os("PKG_CONFIG_PATH_FOR_TARGET").is_none());
}

#[test]
fn wrap_pkg_config_restores_on_panic() {
    let _l = LOCK.get_or_init(|| Mutex::new(())).lock();
    let _env = EnvGuard::new(&["PKG_CONFIG_PATH", "PKG_CONFIG_PATH_FOR_TARGET"]);
    env::set_var("PKG_CONFIG_PATH", "/foo");
    env::remove_var("PKG_CONFIG_PATH_FOR_TARGET");

    let res = std::panic::catch_unwind(|| {
        Library::wrap_pkg_config(tests_dir(), || -> Result<(), pkg_config::Error> {
            std::panic::resume_unwind(Box::new("probe failed"))
        })
    });

    assert!(res.is_err());
    assert_eq!(env::var_os("PKG_CONFIG_PATH").unwrap(), "/foo");
    assert!(env::var_os("PKG_CONFIG_PATH_FOR_TARGET").is_none());
}

#[test]
fn wrap_pkg_config_with_targeted_pkg_config_path() {
    let _l = LOCK.get_or_init(|| Mutex::new(())).lock();
    let targeted = "PKG_CONFIG_PATH_x86_64-unknown-linux-gnu";
    let _env = EnvGuard::new(&[
        "TARGET",
        "HOST",
        targeted,
        "PKG_CONFIG_PATH",
        "PKG_CONFIG_PATH_FOR_TARGET",
    ]);
    env::set_var("TARGET", "x86_64-unknown-linux-gnu");
    env::set_var("HOST", "x86_64-unknown-linux-gnu");
    env::set_var(targeted, "/nonexistent");

    let lib = Library::from_internal_pkg_config(&tests_dir(), "teststaticlib", "0").unwrap();

    assert_eq!(lib.libs[0].name, "teststatic");
    assert_eq!(env::var_os(targeted).unwrap(), "/nonexistent");
}

#[test]
fn wrap_pkg_config_wraps_only_the_targeted_variants_in_use() {
    const HOST: &str = "x86_64-unknown-linux-gnu";
    let _l = LOCK.get_or_init(|| Mutex::new(())).lock();
    let vars = [
        "TARGET",
        "HOST",
        "PKG_CONFIG_PATH",
        "PKG_CONFIG_PATH_FOR_TARGET",
        "PKG_CONFIG_PATH_x86_64-unknown-linux-gnu",
        "PKG_CONFIG_PATH_x86_64_unknown_linux_gnu",
        "PKG_CONFIG_PATH_aarch64-unknown-linux-gnu",
        "PKG_CONFIG_PATH_aarch64_unknown_linux_gnu",
        "HOST_PKG_CONFIG_PATH",
        "TARGET_PKG_CONFIG_PATH",
    ];
    let _env = EnvGuard::new(&vars);
    let pc_dir = tests_dir();
    let user = PathBuf::from("/usr/lib/fake/pkgconfig");

    // (TARGET, variants that are set and must be wrapped, variant that is set and must be left alone)
    let cases = [
        (
            HOST,
            &[
                "PKG_CONFIG_PATH_x86_64-unknown-linux-gnu",
                "PKG_CONFIG_PATH_x86_64_unknown_linux_gnu",
                "HOST_PKG_CONFIG_PATH",
            ][..],
            "TARGET_PKG_CONFIG_PATH",
        ),
        (
            "aarch64-unknown-linux-gnu",
            &["TARGET_PKG_CONFIG_PATH"][..],
            "HOST_PKG_CONFIG_PATH",
        ),
    ];
    for (target, wrapped, ignored) in cases {
        for var in &vars[2..] {
            env::remove_var(var);
        }
        env::set_var("TARGET", target);
        env::set_var("HOST", HOST);
        for var in wrapped.iter().chain([&ignored]) {
            env::set_var(var, &user);
        }

        Library::wrap_pkg_config(&pc_dir, || {
            for var in &vars[2..] {
                let paths = env::var_os(var).map(|v| env::split_paths(&v).collect::<Vec<_>>());
                let expected = if wrapped.contains(var) {
                    Some(vec![pc_dir.clone(), user.clone()])
                } else if *var == ignored {
                    Some(vec![user.clone()])
                } else if *var == "PKG_CONFIG_PATH" {
                    Some(vec![pc_dir.clone()])
                } else {
                    None
                };
                assert_eq!(paths, expected, "{} (TARGET={})", var, target);
            }
            Ok(())
        })
        .unwrap();

        for var in &vars[2..] {
            let expected = (wrapped.contains(var) || *var == ignored).then(|| user.clone().into());
            assert_eq!(env::var_os(var), expected, "{} (TARGET={})", var, target);
        }
    }
}

#[test]
fn probe_without_prebuilt_paths_keeps_pkg_config_path_unset() {
    let _l = LOCK.get_or_init(|| Mutex::new(())).lock();
    let _env = EnvGuard::new(&["PKG_CONFIG_PATH", "PKG_CONFIG_PATH_FOR_TARGET"]);
    env::remove_var("PKG_CONFIG_PATH");

    let mut hash = HashMap::new();
    hash.insert(
        "CARGO_MANIFEST_DIR",
        tests_dir().join("toml-good").to_string_lossy().to_string(),
    );
    #[cfg(feature = "binary")]
    hash.insert("SYSTEM_DEPS_NO_PREBUILT", "".to_string());

    let err = Config::new_with_env(EnvVariables::Mock(hash))
        .probe_full()
        .unwrap_err();
    assert_matches!(
        err,
        Error::PkgConfig(pkg_config::Error::ProbeFailure { command, .. })
            if !command.contains("PKG_CONFIG_PATH=")
    );
}

#[test]
fn build_internal_global_override() {
    let _l = LOCK.get_or_init(|| Mutex::new(())).lock();
    // Request to build all libs using global var but disable it for a specific one
    let called = Rc::new(Cell::new((false, false)));
    let called_clone = called.clone();
    let called_clone2 = called.clone();
    let config = create_config(
        "toml-good",
        vec![
            ("SYSTEM_DEPS_BUILD_INTERNAL", "always"),
            ("SYSTEM_DEPS_TESTLIB_BUILD_INTERNAL", "never"),
        ],
    )
    .add_build_internal("testlib", move |lib, version| {
        let (_, b) = called_clone.get();
        called_clone.replace((true, b));
        let mut pkg_lib = pkg_config::Config::new()
            .print_system_libs(false)
            .cargo_metadata(false)
            .probe(lib)
            .unwrap();
        pkg_lib.version = version.to_string();
        Ok(Library::from_pkg_config(lib, pkg_lib))
    })
    .add_build_internal("testdata", move |lib, version| {
        let (a, _) = called_clone2.get();
        called_clone2.replace((a, true));
        let mut pkg_lib = pkg_config::Config::new()
            .print_system_libs(false)
            .cargo_metadata(false)
            .probe(lib)
            .unwrap();
        pkg_lib.version = version.to_string();
        Ok(Library::from_pkg_config(lib, pkg_lib))
    });

    let libraries = config.probe_full().unwrap();
    assert_eq!(called.get(), (false, true));
    assert!(libraries.get_by_name("testlib").is_some());
    assert!(libraries.get_by_name("testdata").is_some());
}

#[test]
fn build_internal_override_name() {
    let (libraries, called) = test_build_internal(
        "toml-override-name",
        vec![("SYSTEM_DEPS_BUILD_INTERNAL", "always")],
        "testlib",
    )
    .unwrap();

    assert!(called);
    assert!(libraries.get_by_name("test_lib").is_some());
}

#[test]
fn optional() {
    // without any feature, testmore is not optional
    toml_pkg_config_err_version("toml-optional", "2", vec![]);

    // when enabling v3 testmore is now optional
    {
        let _l = LOCK.get_or_init(|| Mutex::new(())).lock();
        let config = create_config("toml-optional", vec![("CARGO_FEATURE_V3", "")]);
        let libs = config.probe_full().unwrap();
        assert!(libs.get_by_name("testlib").is_some());
        assert!(libs.get_by_name("testmore").is_none());
        assert!(libs.get_by_name("testbadger").is_none());
    }

    // testlib is no longer optional if enabling v5
    toml_pkg_config_err_version("toml-optional", "5.0", vec![("CARGO_FEATURE_V5", "")]);
}

#[test]
fn aggregate() {
    let (libraries, _) = toml("toml-two-libs", vec![]).unwrap();

    assert_eq!(libraries.all_libs(), vec!["test", "test2"]);
    assert_eq!(
        libraries.all_link_paths(),
        vec![Path::new("/usr/lib"), Path::new("/usr/lib64")]
    );
    assert_eq!(
        libraries.all_frameworks(),
        vec!["someframework", "someotherframework"]
    );
    assert_eq!(
        libraries.all_framework_paths(),
        vec![Path::new("/usr/lib"), Path::new("/usr/lib64")]
    );
    assert_eq!(
        libraries.all_include_paths(),
        vec![
            Path::new("/usr/include/testanotherlib"),
            Path::new("/usr/include/testlib")
        ]
    );
    assert_eq!(
        libraries.all_defines(),
        vec![
            ("AWESOME", &None),
            ("BADGER", &Some("yes".into())),
            ("GREAT", &None)
        ]
    );
}

#[test]
fn os_specific() {
    let (libraries, _) = toml(
        "toml-os-specific",
        vec![("TARGET", "x86_64-alpine-linux-musl")],
    )
    .unwrap();
    assert!(libraries.get_by_name("testdata").is_some());
    assert!(libraries.get_by_name("testlib").is_some());
    assert!(libraries.get_by_name("testanotherlib").is_some());

    let (libraries, _) = toml("toml-os-specific", vec![("TARGET", "x86_64-apple-darwin")]).unwrap();
    assert!(libraries.get_by_name("testdata").is_none());
    assert!(libraries.get_by_name("testlib").is_none());
    assert!(libraries.get_by_name("testanotherlib").is_some());

    let (libraries, _) = toml(
        "toml-os-specific",
        vec![("TARGET", "x86_64-pc-windows-gnu")],
    )
    .unwrap();
    assert!(libraries.get_by_name("testdata").is_none());
    assert!(libraries.get_by_name("testlib").is_some());
    assert!(libraries.get_by_name("testanotherlib").is_none());
}

#[test]
fn invalid_cfg() {
    let err = toml(
        "toml-invalid-cfg",
        vec![("TARGET", "x86_64-unknown-linux-gnu")],
    )
    .unwrap_err();

    assert_matches!(err, Error::UnsupportedCfg(_));
}

// FIXME(temporary): MSVC's `is_static_available` workaround in lib.rs forces
// returning false to dodge rustc's `ar_archive_writer` u32 overflow when
// bundling huge gst-plugins-rs archives into rlibs. That deliberately stops
// emitting the `static=` modifier on MSVC even when the user requests static
// linking via `SYSTEM_DEPS_*_LINK=static`; the linker still resolves
// `<name>.lib` from the bundle natively, so end linkage stays static — but
// the literal flag contract this test asserts no longer holds. Drop the
// cfg-skip when the upstream fix lands
// (https://github.com/rust-lang/ar_archive_writer/issues/31) or when
// cerbero/gst-plugins-rs starts shipping smaller archives.
#[cfg(not(all(target_os = "windows", target_env = "msvc")))]
#[test]
fn static_one_lib() {
    let (libraries, flags) = toml(
        "toml-static",
        vec![("SYSTEM_DEPS_TESTSTATICLIB_LINK", "static")],
    )
    .unwrap();

    let testdata = libraries.get_by_name("testdata").unwrap();
    assert!(testdata.statik == cfg!(feature = "binary"));

    let testlib = libraries.get_by_name("teststaticlib").unwrap();
    assert!(testlib.statik);

    assert_flags(
        flags,
        r#"cargo:rustc-link-search=native=./src/tests/lib/
cargo:rustc-link-search=framework=./src/tests/lib/
cargo:rustc-link-lib=static=teststatic
cargo:rustc-link-lib=framework=someframework
cargo:include=./src/tests/include/testlib
cargo:rerun-if-env-changed=SYSTEM_DEPS_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTSTATICLIB_LIB
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTSTATICLIB_LIB_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTSTATICLIB_SEARCH_NATIVE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTSTATICLIB_SEARCH_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTSTATICLIB_INCLUDE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTSTATICLIB_LDFLAGS
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTSTATICLIB_NO_PKG_CONFIG
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTSTATICLIB_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTSTATICLIB_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTSTATICLIB_NO_PREBUILT
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LIB
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LIB_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_SEARCH_NATIVE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_SEARCH_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_INCLUDE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LDFLAGS
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_NO_PKG_CONFIG
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_NO_PREBUILT
"#
        .to_string()
        .as_str(),
    );
}

#[test]
fn override_static_no_pkg_config() {
    let (libraries, flags) = toml(
        "toml-static",
        vec![
            ("SYSTEM_DEPS_TESTSTATICLIB_NO_PKG_CONFIG", "1"),
            ("SYSTEM_DEPS_TESTSTATICLIB_LIB", "custom-lib"),
            ("SYSTEM_DEPS_TESTSTATICLIB_LINK", "static"),
        ],
    )
    .unwrap();
    let testlib = libraries.get_by_name("teststaticlib").unwrap();
    assert_eq!(testlib.link_paths, Vec::<PathBuf>::new());
    assert!(testlib.statik);
    assert_eq!(testlib.framework_paths, Vec::<PathBuf>::new());
    assert_eq!(
        testlib.libs,
        vec![InternalLib::new("custom-lib".to_string(), true)]
    );
    assert_eq!(testlib.frameworks, Vec::<String>::new());
    assert_eq!(testlib.include_paths, Vec::<PathBuf>::new());

    assert_flags(
        flags,
        r"cargo:rustc-link-lib=static=custom-lib
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_INCLUDE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LDFLAGS
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LIB
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LIB_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_NO_PKG_CONFIG
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_SEARCH_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_SEARCH_NATIVE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTSTATICLIB_INCLUDE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTSTATICLIB_LDFLAGS
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTSTATICLIB_LIB
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTSTATICLIB_LIB_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTSTATICLIB_NO_PKG_CONFIG
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTSTATICLIB_SEARCH_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTSTATICLIB_SEARCH_NATIVE
cargo:rerun-if-env-changed=SYSTEM_DEPS_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTSTATICLIB_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_NO_PREBUILT
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTSTATICLIB_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTSTATICLIB_NO_PREBUILT
cargo:rerun-if-env-changed=SYSTEM_DEPS_LINK
",
    );
}

// FIXME(temporary): see static_one_lib above; same MSVC workaround quirk.
#[cfg(not(all(target_os = "windows", target_env = "msvc")))]
#[test]
fn static_all_libs() {
    let (libraries, flags) = toml("toml-static", vec![("SYSTEM_DEPS_LINK", "static")]).unwrap();

    let testdata = libraries.get_by_name("testdata").unwrap();
    assert!(testdata.statik);

    let testlib = libraries.get_by_name("teststaticlib").unwrap();
    assert!(testlib.statik);

    assert_flags(
        flags,
        r#"cargo:rustc-link-search=native=./src/tests/lib/
cargo:rustc-link-search=framework=./src/tests/lib/
cargo:rustc-link-lib=static=teststatic
cargo:rustc-link-lib=framework=someframework
cargo:include=./src/tests/include/testlib
cargo:rerun-if-env-changed=SYSTEM_DEPS_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTSTATICLIB_LIB
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTSTATICLIB_LIB_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTSTATICLIB_SEARCH_NATIVE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTSTATICLIB_SEARCH_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTSTATICLIB_INCLUDE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTSTATICLIB_LDFLAGS
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTSTATICLIB_NO_PKG_CONFIG
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTSTATICLIB_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTSTATICLIB_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTSTATICLIB_NO_PREBUILT
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LIB
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LIB_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_SEARCH_NATIVE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_SEARCH_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_INCLUDE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LDFLAGS
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_NO_PKG_CONFIG
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_NO_PREBUILT
"#,
    );
}

#[test]
fn static_lib_not_available() {
    let (libraries, flags) = toml("toml-good", vec![("SYSTEM_DEPS_LINK", "static")]).unwrap();

    let testdata = libraries.get_by_name("testdata").unwrap();
    assert!(testdata.statik);

    // testlib is not available as static library, which is why it is linked dynamically,
    // as seen below
    let testlib = libraries.get_by_name("testlib").unwrap();
    assert!(testlib.statik);

    assert_flags(
        flags,
        r#"cargo:rustc-link-search=native=/usr/lib/
cargo:rustc-link-search=framework=/usr/lib/
cargo:rustc-link-lib=test
cargo:rustc-link-lib=framework=someframework
cargo:include=/usr/include/testlib
cargo:rerun-if-env-changed=SYSTEM_DEPS_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LIB
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LIB_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_SEARCH_NATIVE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_SEARCH_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_INCLUDE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LDFLAGS
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_NO_PKG_CONFIG
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_NO_PREBUILT
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LIB
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LIB_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_SEARCH_NATIVE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_SEARCH_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_INCLUDE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LDFLAGS
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_NO_PKG_CONFIG
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_NO_PREBUILT
"#,
    );
}

#[test]
fn has_link_flags() {
    let (libraries, flags) = toml("toml-rpath", vec![]).unwrap();
    let testlib = libraries.get_by_name("testlibwithrpath").unwrap();
    assert_eq!(testlib.version, "1.2.3");
    assert_eq!(
        testlib.defines.get("BADGER").unwrap().as_deref(),
        Some("yes")
    );
    assert!(testlib.defines.get("AWESOME").unwrap().is_none());

    let testdata = libraries.get_by_name("testdata").unwrap();
    assert_eq!(testdata.version, "4.5.6");
    assert!(libraries.get_by_name("testmore").is_none());

    assert_eq!(libraries.iter().len(), 2);

    assert_flags(
        flags,
        r#"cargo:rustc-link-search=native=/usr/lib/
cargo:rustc-link-search=framework=/usr/lib/
cargo:rustc-link-lib=test
cargo:rustc-link-arg=-Wl,-rpath,/usr/lib/
cargo:rustc-link-lib=framework=someframework
cargo:include=/usr/include/testlib
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_INCLUDE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LDFLAGS
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LIB
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LIB_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_NO_PKG_CONFIG
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_SEARCH_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_SEARCH_NATIVE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIBWITHRPATH_INCLUDE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIBWITHRPATH_LDFLAGS
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIBWITHRPATH_LIB
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIBWITHRPATH_LIB_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIBWITHRPATH_NO_PKG_CONFIG
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIBWITHRPATH_SEARCH_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIBWITHRPATH_SEARCH_NATIVE
cargo:rerun-if-env-changed=SYSTEM_DEPS_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIBWITHRPATH_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTDATA_NO_PREBUILT
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIBWITHRPATH_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIBWITHRPATH_NO_PREBUILT
cargo:rerun-if-env-changed=SYSTEM_DEPS_LINK
"#,
    );
}

#[test]
fn link_files_from_pkg_config() {
    let (libraries, flags) = toml_with_target(
        "toml-link-files",
        vec![("SYSTEM_DEPS_TESTLINKFILES_LINK", "static")],
        "x86_64-unknown-linux-musl",
    )
    .unwrap();

    let testlib = libraries.get_by_name("testlinkfiles").unwrap();
    assert!(testlib.statik);
    assert!(
        !testlib.link_files.is_empty(),
        "link_files should be populated from pkg-config"
    );

    assert_flags(
        flags,
        r#"cargo:rustc-link-search=native=./src/tests/lib
cargo:rustc-link-lib=static=teststatic
cargo:rerun-if-env-changed=SYSTEM_DEPS_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLINKFILES_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLINKFILES_INCLUDE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLINKFILES_LDFLAGS
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLINKFILES_LIB
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLINKFILES_LIB_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLINKFILES_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLINKFILES_NO_PKG_CONFIG
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLINKFILES_NO_PREBUILT
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLINKFILES_SEARCH_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLINKFILES_SEARCH_NATIVE
"#,
    );
}

#[test]
fn link_files_generates_flags() {
    let lib = Library {
        name: "testlib".to_string(),
        source: Source::PkgConfig,
        version: "1.2.3".to_string(),
        link_paths: vec![],
        framework_paths: vec![],
        include_paths: vec![],
        libs: vec![],
        frameworks: vec![],
        defines: HashMap::new(),
        statik: true,
        link_files: vec![
            PathBuf::from("/nix/store/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-boost-static-x86_64-unknown-linux-musl-1.87.0/lib/libboost_container.a"),
            PathBuf::from("/nix/store/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-boost-static-x86_64-unknown-linux-musl-1.87.0/lib/libboost_context.a"),
        ],
        ld_args: vec![],
    };

    let mut deps = Dependencies::default();
    deps.add("testlib", lib);

    let flags = deps.gen_flags(Some("x86_64-unknown-linux-musl")).unwrap();

    assert_flags(
        flags,
        r#"cargo:rustc-link-search=native=/nix/store/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-boost-static-x86_64-unknown-linux-musl-1.87.0/lib
cargo:rustc-link-lib=static=boost_container
cargo:rustc-link-lib=static=boost_context
cargo:rerun-if-env-changed=SYSTEM_DEPS_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_INCLUDE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LDFLAGS
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LIB
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LIB_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_NO_PKG_CONFIG
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_NO_PREBUILT
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_SEARCH_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_SEARCH_NATIVE
"#,
    );
}

#[test]
fn link_files_empty_without_target() {
    let lib = Library {
        name: "testlib".to_string(),
        source: Source::PkgConfig,
        version: "1.2.3".to_string(),
        link_paths: vec![],
        framework_paths: vec![],
        include_paths: vec![],
        libs: vec![],
        frameworks: vec![],
        defines: HashMap::new(),
        statik: false,
        link_files: vec![PathBuf::from("/nix/store/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-boost-1.87.0/lib/libboost_thread.so.1.87.0")],
        ld_args: vec![],
    };

    let mut deps = Dependencies::default();
    deps.add("testlib", lib);

    let flags = deps.gen_flags(None).unwrap();

    assert_flags(
        flags,
        r#"cargo:rerun-if-env-changed=SYSTEM_DEPS_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_BUILD_INTERNAL
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_INCLUDE
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LDFLAGS
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LIB
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LIB_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_LINK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_NO_PKG_CONFIG
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_NO_PREBUILT
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_SEARCH_FRAMEWORK
cargo:rerun-if-env-changed=SYSTEM_DEPS_TESTLIB_SEARCH_NATIVE
"#,
    );
}
