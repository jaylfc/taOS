// SPDX-License-Identifier: AGPL-3.0-or-later
//! Reading GitHub release listings and choosing the assets for each network.
use crate::boards::Network;
use serde_json::Value;

#[derive(Clone, Debug, PartialEq)]
pub struct Asset {
    pub name: String,
    pub url: String,
    pub size: u64,
    /// `sha256:<hex>` digest GitHub computed for the uploaded asset, when present.
    pub sha256: Option<String>,
}

#[derive(Clone, Debug)]
pub struct Release {
    pub tag: String,
    /// Version as the project writes it, without the leading `v` or role prefix.
    pub version: String,
    pub assets: Vec<Asset>,
}

pub fn releases_url(api_base: &str, n: Network) -> String {
    format!("{api_base}/repos/{}/releases?per_page=30", n.repo())
}

fn version_of(n: Network, tag: &str) -> Option<String> {
    let t = match n {
        // MeshCore publishes one release per role; only the companion firmware is used here.
        Network::Meshcore => tag.strip_prefix("companion-")?,
        _ => tag,
    };
    let v = t.strip_prefix('v').unwrap_or(t);
    if v.chars().next().is_some_and(|c| c.is_ascii_digit())
        && v.chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '-')
    {
        Some(v.to_string())
    } else {
        None
    }
}

/// Stable (not draft, not pre-release) releases, newest first as GitHub lists them.
pub fn parse_stable(n: Network, body: &[u8]) -> Result<Vec<Release>, String> {
    let v: Value = serde_json::from_slice(body).map_err(|e| format!("bad release listing: {e}"))?;
    let arr = v
        .as_array()
        .ok_or_else(|| "bad release listing: not a list".to_string())?;
    let mut out = vec![];
    for r in arr {
        if r["draft"].as_bool() != Some(false) || r["prerelease"].as_bool() != Some(false) {
            continue;
        }
        let Some(tag) = r["tag_name"].as_str() else {
            continue;
        };
        let Some(version) = version_of(n, tag) else {
            continue;
        };
        let assets = r["assets"]
            .as_array()
            .map(|a| {
                a.iter()
                    .filter_map(|a| {
                        Some(Asset {
                            name: a["name"].as_str()?.to_string(),
                            url: a["browser_download_url"].as_str()?.to_string(),
                            size: a["size"].as_u64().unwrap_or(0),
                            sha256: a["digest"]
                                .as_str()
                                .and_then(|d| d.strip_prefix("sha256:"))
                                .map(|h| h.to_ascii_lowercase()),
                        })
                    })
                    .collect()
            })
            .unwrap_or_default();
        out.push(Release {
            tag: tag.to_string(),
            version,
            assets,
        });
    }
    Ok(out)
}

/// Latest stable and the previous stable.
pub fn pick_two(mut r: Vec<Release>) -> Vec<Release> {
    r.truncate(2);
    r
}

pub fn find<'a>(r: &'a Release, name: &str) -> Option<&'a Asset> {
    r.assets.iter().find(|a| a.name == name)
}

/// MeshCore companion assets for a board prefix and variant (`ble` or `usb`).
pub fn meshcore_asset<'a>(
    r: &'a Release,
    prefix: &str,
    variant: &str,
    suffix: &str,
) -> Option<&'a Asset> {
    let start = format!("{prefix}_companion_radio_{variant}-v{}-", r.version);
    r.assets
        .iter()
        .find(|a| a.name.starts_with(&start) && a.name.ends_with(suffix))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn body() -> Vec<u8> {
        serde_json::json!([
            {"tag_name":"repeater-v1.18.0","draft":false,"prerelease":false,"assets":[]},
            {"tag_name":"companion-v1.19.0-rc1","draft":false,"prerelease":true,"assets":[]},
            {"tag_name":"companion-v1.18.0","draft":false,"prerelease":false,"assets":[
                {"name":"Heltec_v3_companion_radio_ble-v1.18.0-abc-merged.bin","browser_download_url":"https://github.com/x","size":3,"digest":"sha256:AB"},
                {"name":"Heltec_v3_companion_radio_ble-v1.18.0-abc.bin","browser_download_url":"https://github.com/y","size":3}]},
            {"tag_name":"companion-v1.17.1","draft":false,"prerelease":false,"assets":[]},
            {"tag_name":"companion-v1.17.0","draft":false,"prerelease":false,"assets":[]}
        ])
        .to_string()
        .into_bytes()
    }
    #[test]
    fn meshcore_filters_role_and_prerelease() {
        let r = pick_two(parse_stable(Network::Meshcore, &body()).unwrap());
        assert_eq!(r.len(), 2);
        assert_eq!(r[0].version, "1.18.0");
        assert_eq!(r[1].version, "1.17.1");
        let a = meshcore_asset(&r[0], "Heltec_v3", "ble", "-merged.bin").unwrap();
        assert_eq!(a.sha256.as_deref(), Some("ab"));
        assert!(meshcore_asset(&r[0], "Heltec_v3", "usb", "-merged.bin").is_none());
    }
    #[test]
    fn meshtastic_version_strips_v() {
        let b = serde_json::json!([{"tag_name":"v2.7.26.54e0d8d","draft":false,"prerelease":false,"assets":[]}]).to_string();
        let r = parse_stable(Network::Meshtastic, b.as_bytes()).unwrap();
        assert_eq!(r[0].version, "2.7.26.54e0d8d");
    }
}
