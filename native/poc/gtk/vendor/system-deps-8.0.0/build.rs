pub fn main() -> Result<(), Box<dyn std::error::Error>> {
    #[cfg(feature = "binary")]
    binary::build()?;
    Ok(())
}

#[cfg(feature = "binary")]
mod binary {
    use std::{fs, path::Path};

    use system_deps_meta::{
        binary::{merge, Paths},
        error::{BinaryError, Error},
        parse::read_metadata,
        BUILD_MANIFEST, TARGET_DIR,
    };

    // Add pkg-config paths to the overrides
    pub fn build() -> Result<(), Error> {
        // Read metadata from the crate graph
        let metadata = read_metadata(BUILD_MANIFEST, "system-deps", merge)?;

        // Download the binaries and get their pkg_config paths
        let paths = Paths::from_binaries(metadata)?;

        // Write the binary paths to a file for later use
        let dest = Path::new(TARGET_DIR).join("paths.toml");
        fs::write(&dest, paths.to_string()?).map_err(BinaryError::InvalidDirectory)?;
        println!(
            "cargo:rustc-env=SYSTEM_DEPS_BINARY_PATHS={}",
            dest.display()
        );

        // On Windows the test/run binaries need the prebuilt
        // bin directory on PATH at runtime: rustc-link-search only affects
        // linking, and Cargo doesn't set PATH for executed test binaries.
        // Surface the directories so the user can prepend them.
        #[cfg(windows)]
        {
            let dirs = paths.dll_search_dirs();
            if !dirs.is_empty() {
                let joined =
                    std::env::join_paths(&dirs).expect("DLL search dir contains invalid character");
                let joined = joined.to_string_lossy();
                println!(
                    "cargo:warning=To run tests/binaries, copy & paste (PowerShell): $env:PATH = \"{};$env:PATH\"",
                    joined
                );
                println!(
                    "cargo:warning=To run tests/binaries, copy & paste (cmd.exe):   set \"PATH={};%PATH%\"",
                    joined
                );
            }
        }

        Ok(())
    }
}
