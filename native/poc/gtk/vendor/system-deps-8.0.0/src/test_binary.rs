use std::{
    collections::HashMap,
    fs,
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicUsize, Ordering},
        Mutex, OnceLock,
    },
};

use toml::{Table, Value};

use system_deps_meta::{
    binary::{merge, Paths},
    error::Error,
    parse::read_metadata,
    test::{self, assert_set, Package},
    BUILD_MANIFEST, TARGET_DIR,
};

use crate::{BuildInternalClosureError, Config, EnvVariables, Library};

#[derive(Debug)]
struct Test {
    manifest: PathBuf,
    paths: Paths,
}

impl Test {
    fn new(name: &str, mut packages: Vec<Package>) -> Result<Self, Error> {
        let name = format!("bin_{}", name);
        for p in packages.iter_mut() {
            replace_paths(&name, &mut p.config);
        }

        let manifest = test::Test::write_manifest(name, packages);
        let metadata = read_metadata(&manifest, "system-deps", merge)?;
        let paths = Paths::from_binaries(metadata)?;

        Ok(Self { manifest, paths })
    }
}

fn replace_paths(name: &str, table: &mut Table) {
    static COUNT: AtomicUsize = AtomicUsize::new(0);

    for (k, v) in table.iter_mut() {
        match v {
            Value::String(v) if k == "url" && v == "$TEST" => {
                let folder = COUNT.fetch_add(1, Ordering::Relaxed);
                let dir = format!("{}/paths/{}/{}", env!("OUT_DIR"), name, folder);
                fs::create_dir_all(&dir).expect("Failed to create test paths");
                *v = format!("file://{}", dir);
            }
            Value::Table(v) => replace_paths(name, v),
            _ => (),
        }
    }
}

/// Convert a path to a URL-safe string by replacing backslashes with forward slashes.
/// This is necessary on Windows where `Path::display()` uses `\`, but TOML interprets
/// backslashes as escape sequences (e.g. `\U` as unicode escape).
fn path_to_url(path: &Path) -> String {
    path.display().to_string().replace('\\', "/")
}

fn assert_paths(paths: Option<&Vec<PathBuf>>, expected: &[&str]) {
    assert_set(
        paths.into_iter().flatten(),
        &expected
            .iter()
            .map(|s| Path::new(TARGET_DIR).join(s))
            .collect::<Vec<_>>(),
    )
}

fn get_archives(web: Option<&str>) -> (PathBuf, Vec<(Table, &str, String, &str)>) {
    let base_path = Path::new(BUILD_MANIFEST)
        .parent()
        .unwrap()
        .join("src/tests");

    let mut archives = vec![
        #[cfg(feature = "gz")]
        (
            if web.is_some() { "web_gz" } else { "gz" },
            "test.tar.gz",
            "5135f7d6b869ae8802228aed4328f2aecf8f38ba597da89ced03b30d6afc3a35",
        ),
        #[cfg(feature = "xz")]
        (
            if web.is_some() { "web_xz" } else { "xz" },
            "test.tar.xz",
            "9b15167891c06d78995781683e9f5db58091a1678b9cedc9f2e04a53b549166e",
        ),
        #[cfg(feature = "zip")]
        (
            if web.is_some() { "web_zip" } else { "zip" },
            "test.zip",
            "cc4f4303d8673b3265ed92c7fbdbbe840b6f96f1e24d6bb92b3990f0c2238b9d",
        ),
    ];

    if web.is_none() {
        archives.push(("test", "uninstalled", ""));
    }

    let archives = archives
        .into_iter()
        .map(|(name, url, checksum)| {
            let url = if let Some(ref server) = web {
                format!("http://{}/{}", server, url)
            } else {
                format!("file://{}", path_to_url(&base_path.join(url)))
            };

            let manifest = format!(
                r#"
                    [package.metadata.system-deps.{}]
                    name = "{}"
                    version = "1.2.3"
                    url = "{}"
                    checksum = "{}"
                    {}"#,
                name,
                name,
                url,
                checksum,
                if name == "test" {
                    // To test the info.toml
                    ""
                } else {
                    r#"paths = ["lib/pkgconfig"]"#
                }
            );

            (
                toml::from_str(&manifest).unwrap(),
                name,
                url.strip_prefix("file://").unwrap_or_default().to_string(),
                checksum,
            )
        })
        .collect();

    (base_path, archives)
}

#[test]
fn simple() -> Result<(), Error> {
    let pkgs = vec![Package {
        name: "dep",
        deps: vec![],
        config: toml::toml![
            [package.metadata.system-deps.dep]
            url = "$TEST"
            paths = [ "lib/pkgconfig" ]
        ],
    }];

    let test = Test::new("simple", pkgs)?;
    assert_paths(test.paths.get("dep"), &["dep/lib/pkgconfig"]);

    Ok(())
}

#[test]
fn overrides() -> Result<(), Error> {
    let pkgs = vec![
        Package {
            name: "pkg",
            deps: vec!["dep"],
            config: toml::toml![
                [package.metadata.system-deps.dep]
                url = "$TEST"
                paths = [ "new" ]
            ],
        },
        Package {
            name: "dep",
            deps: vec![],
            config: toml::toml![
                [package.metadata.system-deps.dep]
                url = "$TEST"
                paths = [ "old" ]
            ],
        },
    ];

    let test = Test::new("overrides", pkgs)?;
    assert_paths(test.paths.get("dep"), &["dep/new", "dep/old"]);

    Ok(())
}

#[test]
fn provides() -> Result<(), Error> {
    let pkgs = vec![
        Package {
            name: "pkg",
            deps: vec!["dep"],
            config: toml::toml![
                [package.metadata.system-deps.pkg]
                name = "pkg"
                url = "$TEST"
                paths = [ "lib/pkgconfig" ]
                provides = [ "dep" ]
            ],
        },
        Package {
            name: "dep",
            deps: vec![],
            config: toml::toml![
                [package.metadata.system-deps.dep]
                url = "$TEST"
                name = "dep"
            ],
        },
    ];

    let test = Test::new("provides", pkgs)?;
    assert_paths(test.paths.get("pkg"), &["pkg/lib/pkgconfig"]);
    assert_paths(test.paths.get("dep"), &["pkg/lib/pkgconfig"]);

    Ok(())
}

#[test]
fn provides_override() -> Result<(), Error> {
    let pkgs = vec![
        Package {
            name: "main",
            deps: vec!["pkg"],
            config: toml::toml![
                [package.metadata.system-deps.dep]
                url = "$TEST"
                paths = [ "lib/pkgconfig" ]
            ],
        },
        Package {
            name: "pkg",
            deps: vec!["dep"],
            config: toml::toml![
                [package.metadata.system-deps.pkg]
                name = "pkg"
                url = "$TEST"
                paths = [ "lib/pkgconfig" ]
                provides = [ "dep" ]
            ],
        },
        Package {
            name: "dep",
            deps: vec![],
            config: toml::toml![
                [package.metadata.system-deps.dep]
                name = "dep"
            ],
        },
    ];

    let test = Test::new("provides_override", pkgs)?;
    assert_paths(test.paths.get("pkg"), &["pkg/lib/pkgconfig"]);
    assert_paths(test.paths.get("dep"), &["dep/lib/pkgconfig"]);

    Ok(())
}

#[test]
fn provides_conflict() -> Result<(), Error> {
    let pkgs = vec![
        Package {
            name: "main",
            deps: vec!["a", "b"],
            config: Default::default(),
        },
        Package {
            name: "a",
            deps: vec!["dep"],
            config: toml::toml![
                [package.metadata.system-deps.a]
                name = "a"
                url = "$TEST"
                paths = [ "lib/pkgconfig" ]
                provides = [ "dep" ]
            ],
        },
        Package {
            name: "b",
            deps: vec!["dep"],
            config: toml::toml![
                [package.metadata.system-deps.dep]
                url = "$TEST"
                paths = [ "lib/pkgconfig" ]
            ],
        },
        Package {
            name: "dep",
            deps: vec![],
            config: toml::toml![
                [package.metadata.system-deps.dep]
                name = "dep"
            ],
        },
    ];

    let res = Test::new("provides_conflict", pkgs);
    println!("left: {:?}", res);
    assert!(matches!(res, Err(Error::IncompatibleMerge)));

    Ok(())
}

#[test]
fn provides_wildcard() -> Result<(), Error> {
    let mut pkgs = vec![
        Package {
            name: "pkg",
            deps: vec!["dep1", "dep2"],
            config: toml::toml![
                [package.metadata.system-deps.pkg]
                name = "pkg"
                url = "$TEST"
                paths = [ "lib/pkgconfig" ]
                provides = [ "dep*" ]
            ],
        },
        Package {
            name: "dep1",
            deps: vec![],
            config: toml::toml![
                [package.metadata.system-deps.dep1]
                name = "dep1"
            ],
        },
        Package {
            name: "dep2",
            deps: vec![],
            config: toml::toml![
                [package.metadata.system-deps.dep2]
                name = "dep2"
            ],
        },
    ];

    let test = Test::new("provides_wildcard", pkgs.clone())?;
    assert_paths(test.paths.get("pkg"), &["pkg/lib/pkgconfig"]);
    assert_paths(test.paths.get("dep1"), &["pkg/lib/pkgconfig"]);
    assert_paths(test.paths.get("dep2"), &["pkg/lib/pkgconfig"]);

    pkgs.insert(
        0,
        Package {
            name: "main",
            deps: vec!["pkg"],
            config: toml::toml![
                [package.metadata.system-deps.dep2]
                url = "$TEST"
                paths = [ "lib/pkgconfig" ]
            ],
        },
    );

    let test = Test::new("provides_wildcard_overwrite", pkgs)?;
    assert_paths(test.paths.get("pkg"), &["pkg/lib/pkgconfig"]);
    assert_paths(test.paths.get("dep1"), &["pkg/lib/pkgconfig"]);
    assert_paths(test.paths.get("dep2"), &["dep2/lib/pkgconfig"]);

    Ok(())
}

#[test]
fn file_types() -> Result<(), Error> {
    let (_, archives) = get_archives(None);

    for (config, name, url, checksum) in archives {
        let pkgs = vec![Package {
            name,
            deps: vec![],
            config,
        }];

        let test = Test::new(name, pkgs)?;
        let paths = test.paths.get(name).expect("There should be a path");
        assert!(paths.len() == 1);
        let mut p = paths[0].clone();

        assert!(p.join("test.pc").is_file());
        p.pop();
        assert!(p.join("libtest.a").is_file());
        p.pop();

        if name == "test" {
            // Local folder
            assert_eq!(p.read_link().unwrap(), Path::new(&url));
        } else {
            p.push("checksum");
            assert!(p.is_file());
            assert_eq!(checksum.to_string(), fs::read_to_string(p).unwrap());
        }
    }

    Ok(())
}

#[test]
fn unsupported_extensions() -> Result<(), Error> {
    let mut pkgs = vec![Package {
        name: "dep",
        deps: vec![],
        config: toml::toml![
            [package.metadata.system-deps.dep]
            url = "http://unsuported.ext"
        ],
    }];

    assert!(Test::new("unsupported_extension", pkgs.clone()).is_err());

    pkgs[0].config = toml::toml![
        [package.metadata.system-deps.dep]
        url = "http://no_ext"
    ];

    assert!(Test::new("no_extension", pkgs).is_err());

    Ok(())
}

#[test]
fn invalid_checksum() -> Result<(), Error> {
    let base_path = get_archives(None).0;
    let pkgs = vec![Package {
        name: "checksum",
        deps: vec![],
        config: toml::from_str(&format!(
            r#"
                [package.metadata.system-deps.not_found]
                url = "file://{}/test.zip"
                checksum = "1234"
            "#,
            path_to_url(&base_path)
        ))?,
    }];

    assert!(Test::new("invalid_checksum", pkgs).is_err());

    Ok(())
}

#[test]
#[cfg(any(feature = "gz", feature = "xz", feature = "zip"))]
fn download() -> Result<(), Error> {
    use std::{convert::TryInto, sync::Arc, thread, time::Duration};
    use system_deps_meta::binary::Extension;
    use tiny_http::{Header, Response, Server, StatusCode};

    let server_url = "127.0.0.1:8000";
    let (base_path, archives) = get_archives(Some(server_url));

    let mut path_list = thread::scope(|s| -> Result<Vec<(PathBuf, String)>, Error> {
        let handle = Arc::new(Server::http(server_url).unwrap());

        let server = handle.clone();

        s.spawn(move || loop {
            let Ok(Some(req)) = server.recv_timeout(Duration::new(3, 0)) else {
                break;
            };

            let url = base_path.join(&req.url()[1..]);

            let content_type = match url.as_path().try_into().unwrap() {
                #[cfg(feature = "gz")]
                Extension::TarGz => "application/gzip",
                #[cfg(feature = "xz")]
                Extension::TarXz => "application/zlib",
                #[cfg(feature = "zip")]
                Extension::Zip => "application/zip",
                _ => unreachable!(),
            };
            let header = Header {
                field: "Content-Type".parse().unwrap(),
                value: std::str::FromStr::from_str(content_type).unwrap(),
            };

            match fs::File::open(url) {
                Ok(file) => {
                    let res = Response::from_file(file).with_header(header);
                    let _ = req.respond(res);
                }
                Err(_) => {
                    let res = Response::new_empty(StatusCode(404));
                    let _ = req.respond(res);
                }
            };
        });

        let mut path_list = Vec::new();
        for (config, name, _, checksum) in archives {
            let pkgs = vec![Package {
                name,
                deps: vec![],
                config,
            }];

            let test = Test::new(name, pkgs)?;
            let paths = test.paths.get(name).expect("There should be a path");
            assert!(paths.len() == 1);
            path_list.push((paths[0].clone(), checksum.to_string()));
        }

        let pkgs = vec![Package {
            name: "not_found",
            deps: vec![],
            config: toml::from_str(&format!(
                r#"
                [package.metadata.system-deps.not_found]
                url = "http://{}/not_found.zip"
            "#,
                server_url
            ))?,
        }];
        assert!(Test::new("not_found", pkgs).is_err());

        handle.unblock();
        Ok(path_list)
    })?;

    for (p, ch) in path_list.iter_mut() {
        assert!(p.join("test.pc").is_file());
        p.pop();
        assert!(p.join("libtest.a").is_file());
        p.pop();

        p.push("checksum");
        assert!(p.is_file());
        assert_eq!(*ch, fs::read_to_string(p).unwrap());
    }
    Ok(())
}

#[test]
fn probe() -> Result<(), Error> {
    let _l = crate::test::LOCK.get_or_init(|| Mutex::new(())).lock();
    static PATHS: OnceLock<Paths> = OnceLock::new();

    let pkgs = vec![Package {
        name: "test",
        deps: vec![],
        config: get_archives(None).1.into_iter().last().unwrap().0,
    }];

    let test = Test::new("probe", pkgs)?;

    let mut config = Config::new_with_env(EnvVariables::Mock(HashMap::from([(
        "CARGO_MANIFEST_DIR",
        test.manifest.parent().unwrap().to_string_lossy().into(),
    )])));

    let test_path = test.paths.get("test").unwrap().clone();
    config.paths = PATHS.get_or_init(|| test.paths);
    assert_eq!(config.query_path("test").unwrap(), &test_path);

    let libs = config.probe_full().unwrap();
    let testlib = libs.get_by_name("test").unwrap();
    assert_eq!(testlib.version, "1.2.3");
    assert!(testlib.statik);

    Ok(())
}

#[test]
fn internal_pkg_config() -> Result<(), Error> {
    let _l = crate::test::LOCK.get_or_init(|| Mutex::new(())).lock();

    let pkgs = vec![Package {
        name: "test",
        deps: vec![],
        config: get_archives(None).1.into_iter().last().unwrap().0,
    }];

    let test = Test::new("internal_pkg_config", pkgs)?;
    let paths = test.paths.get("test").unwrap();

    let lib = Library::from_internal_pkg_config(paths, "test", "1.0").unwrap();
    assert_eq!(lib.version, "1.2.3");

    let lib = Library::from_internal_pkg_config(paths, "test", "2.0");
    println!("left: {:?}", lib);
    assert!(matches!(lib, Err(BuildInternalClosureError::PkgConfig(_))));

    Ok(())
}

#[test]
#[ignore = "per-version binary urls not yet implemented"]
fn library_versions() -> Result<(), Error> {
    let base_path = get_archives(None).0;
    let pkgs = vec![Package {
        name: "dep",
        deps: vec![],
        config: toml::from_str(&format!(
            r#"
            [package.metadata.system-deps.dep]
            version = "1.0"
            url = "$TEST"
            paths = [ "lib/pkgconfig" ]
            v2_0 = {{ version = "2.0", url = "file://{}/test.zip" }}
        "#,
            path_to_url(&base_path)
        ))?,
    }];

    let _test = Test::new("library_versions", pkgs)?;
    // assert_paths(test.paths.get("dep"), &["dep/lib/pkgconfig"]);
    todo!();
}
