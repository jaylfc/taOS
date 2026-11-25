// SPDX-License-Identifier: AGPL-3.0-or-later
//! Network access. All requests go through the system `curl` with an argument list (no shell),
//! to an allow-listed set of GitHub hosts. Tests point `TAOS_FW_API_BASE` at a local fake server.
use std::fs;
use std::path::Path;
use std::process::{Command, Stdio};

pub const DEFAULT_API: &str = "https://api.github.com";
const ALLOWED_HOSTS: [&str; 5] = [
    "api.github.com",
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "raw.githubusercontent.com",
];

#[derive(Debug, Default, Clone)]
pub struct HttpResponse {
    pub status: u16,
    pub etag: Option<String>,
    pub ratelimit_remaining: Option<u64>,
    pub ratelimit_reset: Option<u64>,
    pub retry_after: Option<u64>,
    pub body: Vec<u8>,
}

pub trait Fetcher {
    fn api_base(&self) -> String;
    fn get(&self, url: &str, etag: Option<&str>) -> Result<HttpResponse, String>;
    fn download(&self, url: &str, dest: &Path) -> Result<(), String>;
}

fn host_of(url: &str) -> Option<(&str, &str)> {
    let (scheme, rest) = url.split_once("://")?;
    let auth = rest.split(['/', '?', '#']).next()?;
    if auth.contains('@') {
        return None;
    }
    Some((scheme, auth))
}

pub struct Curl {
    pub api_base: String,
    /// Private scratch area (inside the 0700 data directory) for header and body files.
    pub tmp: std::path::PathBuf,
}

impl Curl {
    pub fn from_env(tmp: std::path::PathBuf) -> Result<Curl, String> {
        let api_base = std::env::var("TAOS_FW_API_BASE").unwrap_or_else(|_| DEFAULT_API.into());
        let c = Curl { api_base, tmp };
        if !c.url_allowed(&format!("{}/x", c.api_base)) {
            return Err(
                "TAOS_FW_API_BASE must be https on a GitHub host, or http on 127.0.0.1".into(),
            );
        }
        Ok(c)
    }
    pub fn url_allowed(&self, url: &str) -> bool {
        let Some((scheme, auth)) = host_of(url) else {
            return false;
        };
        let host = auth.rsplit_once(':').map_or(auth, |(h, _)| h);
        if scheme == "http" {
            return host == "127.0.0.1";
        }
        scheme == "https" && ALLOWED_HOSTS.contains(&host)
    }
    fn check(&self, url: &str) -> Result<(), String> {
        if self.url_allowed(url) {
            Ok(())
        } else {
            Err(format!(
                "refusing to fetch from a non-GitHub address: {url}"
            ))
        }
    }
}

fn parse_headers(raw: &str) -> HttpResponse {
    // With redirects curl writes several header blocks; the last block is the final response.
    let block = raw
        .split("\r\n\r\n")
        .filter(|b| !b.trim().is_empty())
        .last()
        .unwrap_or("");
    let mut r = HttpResponse::default();
    for (i, line) in block.lines().enumerate() {
        if i == 0 {
            r.status = line
                .split_whitespace()
                .nth(1)
                .and_then(|s| s.parse().ok())
                .unwrap_or(0);
            continue;
        }
        let Some((k, v)) = line.split_once(':') else {
            continue;
        };
        let v = v.trim();
        match k.trim().to_ascii_lowercase().as_str() {
            "etag" => r.etag = Some(v.to_string()),
            "x-ratelimit-remaining" => r.ratelimit_remaining = v.parse().ok(),
            "x-ratelimit-reset" => r.ratelimit_reset = v.parse().ok(),
            "retry-after" => r.retry_after = v.parse().ok(),
            _ => {}
        }
    }
    r
}

impl Fetcher for Curl {
    fn api_base(&self) -> String {
        self.api_base.clone()
    }
    fn get(&self, url: &str, etag: Option<&str>) -> Result<HttpResponse, String> {
        self.check(url)?;
        static SEQ: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(0);
        let dir = self.tmp.join(format!(
            "curl-{}-{}",
            std::process::id(),
            SEQ.fetch_add(1, std::sync::atomic::Ordering::SeqCst)
        ));
        fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
        let (hp, bp) = (dir.join("h"), dir.join("b"));
        let mut a: Vec<String> = vec![
            "-sS".into(),
            "-L".into(),
            "--proto".into(),
            "=https,http".into(),
            "--proto-redir".into(),
            "=https".into(),
            "--max-redirs".into(),
            "3".into(),
            "--connect-timeout".into(),
            "15".into(),
            "--max-time".into(),
            "90".into(),
            "-H".into(),
            "Accept: application/vnd.github+json".into(),
            "-H".into(),
            "User-Agent: taos-firmware".into(),
            "-D".into(),
            hp.display().to_string(),
            "-o".into(),
            bp.display().to_string(),
        ];
        if let Some(e) = etag {
            a.push("-H".into());
            a.push(format!("If-None-Match: {e}"));
        }
        a.push(url.into());
        let out = Command::new("curl")
            .args(&a)
            .stdin(Stdio::null())
            .output()
            .map_err(|e| format!("cannot run curl: {e}"))?;
        let res = if out.status.success() {
            let mut r = parse_headers(&fs::read_to_string(&hp).unwrap_or_default());
            r.body = fs::read(&bp).unwrap_or_default();
            Ok(r)
        } else {
            Err(format!(
                "network error: {}",
                String::from_utf8_lossy(&out.stderr).trim()
            ))
        };
        let _ = fs::remove_dir_all(&dir);
        res
    }
    fn download(&self, url: &str, dest: &Path) -> Result<(), String> {
        self.check(url)?;
        let out = Command::new("curl")
            .args([
                "-sS",
                "-L",
                "--fail",
                "--proto",
                "=https,http",
                "--proto-redir",
                "=https",
                "--max-redirs",
                "5",
                "--connect-timeout",
                "20",
                "--max-time",
                "1800",
                "--max-filesize",
                "800000000",
                "-H",
                "User-Agent: taos-firmware",
                "-o",
            ])
            .arg(dest)
            .arg(url)
            .stdin(Stdio::null())
            .output()
            .map_err(|e| format!("cannot run curl: {e}"))?;
        if out.status.success() {
            Ok(())
        } else {
            let _ = fs::remove_file(dest);
            Err(format!(
                "download failed: {}",
                String::from_utf8_lossy(&out.stderr).trim()
            ))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn allow_list() {
        let c = Curl {
            api_base: DEFAULT_API.into(),
            tmp: std::env::temp_dir(),
        };
        assert!(c.url_allowed("https://api.github.com/repos/a/b/releases"));
        assert!(c.url_allowed("https://github.com/a/b/releases/download/v1/x.zip"));
        assert!(c.url_allowed("http://127.0.0.1:9/x"));
        assert!(!c.url_allowed("http://github.com/x"));
        assert!(!c.url_allowed("https://evil.example/x"));
        assert!(!c.url_allowed("https://github.com@evil.example/x"));
        assert!(!c.url_allowed("https://github.com.evil.example/x"));
        assert!(!c.url_allowed("file:///etc/passwd"));
    }
    #[test]
    fn headers_last_block() {
        let r = parse_headers("HTTP/1.1 302 Found\r\nx: y\r\n\r\nHTTP/2 200\r\nETag: \"abc\"\r\nX-RateLimit-Remaining: 7\r\n\r\n");
        assert_eq!(r.status, 200);
        assert_eq!(r.etag.as_deref(), Some("\"abc\""));
        assert_eq!(r.ratelimit_remaining, Some(7));
    }
}
