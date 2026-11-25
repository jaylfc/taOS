// SPDX-License-Identifier: AGPL-3.0-or-later
//! The firmware library: fetch the latest stable and the previous stable release of each network,
//! verify them, and record them in the manifest. Nothing is ever bundled with taOS.
use crate::boards::{Board, Family, Network, BOARDS, NETWORKS};
use crate::net::Fetcher;
use crate::sources::{self, Asset, Release};
use crate::store::{self, Entry, FileRec, Licences, Manifest, Paths};
use crate::timefmt;
use serde::Serialize;
use serde_json::Value;
use std::collections::BTreeMap;
use std::fs::{self, File};
use std::io::Read;
use std::path::{Path, PathBuf};

#[derive(Default)]
pub struct RefreshOpts {
    pub network: Option<Network>,
    pub board: Option<String>,
    /// Forget earlier failures and try everything again.
    pub force: bool,
}

#[derive(Serialize, Debug, Default)]
pub struct NetReport {
    pub network: String,
    /// `ok`, `licence_required`, `offline`, `rate_limited`, `error`
    pub status: String,
    pub detail: String,
    pub fetched: Vec<String>,
    pub skipped: usize,
    pub errors: Vec<String>,
    pub retry_after_epoch: Option<u64>,
    /// Things that are normal but worth knowing, for example a release too old to flash.
    pub notes: Vec<String>,
}

#[derive(Default, serde::Serialize, serde::Deserialize)]
struct State {
    etags: BTreeMap<String, String>,
    /// Network id -> epoch seconds before which the API must not be called again.
    blocked_until: BTreeMap<String, u64>,
    /// Entry id -> (epoch seconds until which it is not retried, why). A failed download or an
    /// unusable release is not fetched again on every timer tick.
    skip: BTreeMap<String, (u64, String)>,
}

const RETRY_AFTER_FAILURE: u64 = 6 * 3600;

impl State {
    fn skipped(&self, id: &str) -> bool {
        self.skip
            .get(id)
            .is_some_and(|(t, _)| *t > timefmt::now_secs())
    }
    /// Record a failure for an entry: temporary, or for good when the release can never be used.
    fn fail(&mut self, rep: &mut NetReport, id: &str, tag: &str, msg: String) {
        if let Some(why) = msg.strip_prefix("unsupported:") {
            self.skip.insert(id.into(), (u64::MAX, why.trim().into()));
            rep.notes.push(format!("{id}: {}", why.trim()));
        } else {
            self.skip.insert(
                id.into(),
                (timefmt::now_secs() + RETRY_AFTER_FAILURE, msg.clone()),
            );
            rep.errors.push(format!("{tag} {id}: {msg}"));
        }
    }
}

fn load_state(p: &Paths) -> State {
    fs::read(p.state())
        .ok()
        .and_then(|b| serde_json::from_slice(&b).ok())
        .unwrap_or_default()
}

fn save_state(p: &Paths, s: &State) -> Result<(), String> {
    store::write_atomic(
        &p.state(),
        &serde_json::to_vec_pretty(s).map_err(|e| e.to_string())?,
    )
}

fn cache_file(p: &Paths, url: &str) -> PathBuf {
    p.http_cache().join(format!(
        "{}.json",
        &store::sha256_bytes(url.as_bytes())[..24]
    ))
}

enum Listing {
    Body(Vec<u8>),
    Offline(String),
    Limited(Option<u64>, String),
    Error(String),
}

fn listing(p: &Paths, f: &dyn Fetcher, st: &mut State, n: Network) -> Listing {
    let now = timefmt::now_secs();
    if let Some(&t) = st.blocked_until.get(n.id()) {
        if t > now {
            return Listing::Limited(Some(t), "rate limit window still open".into());
        }
    }
    let url = sources::releases_url(&f.api_base(), n);
    let cache = cache_file(p, &url);
    let etag = st
        .etags
        .get(&url)
        .filter(|_| cache.exists())
        .map(String::as_str);
    let r = match f.get(&url, etag) {
        Ok(r) => r,
        Err(e) if e.starts_with("cannot run curl") => return Listing::Error(e),
        Err(e) => return Listing::Offline(e),
    };
    match r.status {
        200 => {
            if let Err(e) = store::write_atomic(&cache, &r.body) {
                return Listing::Error(e);
            }
            if let Some(e) = r.etag {
                st.etags.insert(url, e);
            }
            Listing::Body(r.body)
        }
        304 => match fs::read(&cache) {
            Ok(b) => Listing::Body(b),
            Err(e) => Listing::Error(format!("cached listing missing: {e}")),
        },
        403 | 429 => {
            let until = r
                .ratelimit_reset
                .or(r.retry_after.map(|s| now + s))
                .unwrap_or(now + 3600);
            let until = until.max(now + 60);
            st.blocked_until.insert(n.id().into(), until);
            Listing::Limited(
                Some(until),
                format!(
                    "GitHub rate limit (HTTP {}); not asking again until it resets",
                    r.status
                ),
            )
        }
        s => Listing::Error(format!("GitHub answered HTTP {s}")),
    }
}

pub fn refresh(p: &Paths, f: &dyn Fetcher, opts: &RefreshOpts) -> Vec<NetReport> {
    let mut out = vec![];
    if let Err(e) = p.ensure() {
        return vec![NetReport {
            network: "all".into(),
            status: "error".into(),
            detail: e,
            ..Default::default()
        }];
    }
    let mut st = load_state(p);
    if opts.force {
        st.skip.clear();
    }
    for n in NETWORKS {
        if opts.network.is_some_and(|o| o != n) {
            continue;
        }
        out.push(refresh_one(p, f, &mut st, n, opts));
    }
    if let Err(e) = save_state(p, &st) {
        out.push(NetReport {
            network: "state".into(),
            status: "error".into(),
            detail: e,
            ..Default::default()
        });
    }
    out
}

fn refresh_one(
    p: &Paths,
    f: &dyn Fetcher,
    st: &mut State,
    n: Network,
    opts: &RefreshOpts,
) -> NetReport {
    let mut rep = NetReport {
        network: n.id().into(),
        status: "ok".into(),
        ..Default::default()
    };
    match Licences::load(p) {
        Ok(l) if l.get(n.id()).is_some() => {}
        Ok(_) => {
            rep.status = "licence_required".into();
            rep.detail = format!(
                "the {} licence ({}) has not been accepted yet: {}",
                n.display(),
                n.licence_id(),
                n.licence_url()
            );
            return rep;
        }
        Err(e) => {
            rep.status = "error".into();
            rep.detail = e;
            return rep;
        }
    }
    let body = match listing(p, f, st, n) {
        Listing::Body(b) => b,
        Listing::Offline(e) => {
            rep.status = "offline".into();
            rep.detail = e;
            return rep;
        }
        Listing::Limited(t, d) => {
            rep.status = "rate_limited".into();
            rep.detail = d;
            rep.retry_after_epoch = t;
            return rep;
        }
        Listing::Error(e) => {
            rep.status = "error".into();
            rep.detail = e;
            return rep;
        }
    };
    let rels = match sources::parse_stable(n, &body) {
        Ok(r) => sources::pick_two(r),
        Err(e) => {
            rep.status = "error".into();
            rep.detail = e;
            return rep;
        }
    };
    let mut man = match Manifest::load(p) {
        Ok(m) => m,
        Err(e) => {
            rep.status = "error".into();
            rep.detail = e;
            return rep;
        }
    };
    let boards: Vec<&Board> = BOARDS
        .iter()
        .filter(|b| b.supports(n))
        .filter(|b| opts.board.as_deref().is_none_or(|o| o == b.id))
        .collect();
    for rel in &rels {
        let before = man.entries.len();
        build_release(p, f, st, n, rel, &boards, &mut man, &mut rep);
        if man.entries.len() != before {
            if let Err(e) = man.save(p) {
                rep.errors.push(e);
            }
        }
    }
    prune(p, n, &rels, &boards, &mut man, &mut rep);
    if let Some(first) = rep.fetched.first() {
        let v = first.split(':').nth(2).unwrap_or("");
        if let Err(e) = Licences::note_version(p, n.id(), v) {
            rep.errors.push(e);
        }
    }
    if let Err(e) = man.save(p) {
        rep.errors.push(e);
    }
    if !rep.errors.is_empty() {
        rep.status = "error".into();
        rep.detail = format!("{} problem(s); see errors", rep.errors.len());
    }
    rep
}

fn have(p: &Paths, man: &Manifest, id: &str) -> bool {
    man.get(id)
        .is_some_and(|e| store::verify_entry(p, e).is_ok())
}

pub fn entry_id(n: Network, board: &str, version: &str, variant: &str) -> String {
    if variant.is_empty() {
        format!("{}:{board}:{version}", n.id())
    } else {
        format!("{}:{board}:{version}:{variant}", n.id())
    }
}

pub fn safe_name(s: &str) -> bool {
    !s.is_empty()
        && s != "."
        && s != ".."
        && s.chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-'))
}

struct Fetched {
    path: PathBuf,
    sha256: String,
    verification: &'static str,
    unsigned: bool,
}

/// Download an asset to the temp area and check it against the release host's digest and, when the
/// project publishes one, the publisher checksum. A mismatch deletes the file and returns an error.
fn fetch_asset(
    p: &Paths,
    f: &dyn Fetcher,
    a: &Asset,
    publisher: Option<&str>,
) -> Result<Fetched, String> {
    if !safe_name(&a.name) {
        return Err(format!("unsafe asset name {:?}", a.name));
    }
    let tmp = p
        .tmp()
        .join(format!("dl-{}-{}", std::process::id(), a.name));
    f.download(&a.url, &tmp)?;
    let (h, size) = store::sha256_file(&tmp)?;
    let bad = |why: String| {
        let _ = fs::remove_file(&tmp);
        Err(format!("{}: {why}; file discarded", a.name))
    };
    if a.size != 0 && a.size != size {
        return bad(format!("size {size} differs from the listed {}", a.size));
    }
    if let Some(d) = &a.sha256 {
        if *d != h {
            return bad(format!(
                "sha256 {h} differs from the release host digest {d}"
            ));
        }
    }
    if let Some(d) = publisher {
        if !d.eq_ignore_ascii_case(&h) {
            return bad(format!(
                "sha256 {h} differs from the publisher checksum {d}"
            ));
        }
    }
    let (verification, unsigned) = if publisher.is_some() {
        ("publisher-checksum", false)
    } else if a.sha256.is_some() {
        ("release-host-digest", true)
    } else {
        ("unsigned-by-publisher", true)
    };
    Ok(Fetched {
        path: tmp,
        sha256: h,
        verification,
        unsigned,
    })
}

fn place_bytes(
    p: &Paths,
    n: Network,
    version: &str,
    name: &str,
    data: &[u8],
) -> Result<(String, String, u64), String> {
    if !safe_name(name) || !safe_name(version) {
        return Err(format!("unsafe file name {name:?}"));
    }
    let dir = p.library().join(n.id()).join(version);
    fs::create_dir_all(&dir).map_err(|e| format!("create {}: {e}", dir.display()))?;
    store::write_atomic(&dir.join(name), data)?;
    Ok((
        format!("{}/{version}/{name}", n.id()),
        store::sha256_bytes(data),
        data.len() as u64,
    ))
}

fn place_file(
    p: &Paths,
    n: Network,
    version: &str,
    name: &str,
    from: &Path,
) -> Result<(String, String, u64), String> {
    if !safe_name(name) || !safe_name(version) {
        return Err(format!("unsafe file name {name:?}"));
    }
    let dir = p.library().join(n.id()).join(version);
    fs::create_dir_all(&dir).map_err(|e| format!("create {}: {e}", dir.display()))?;
    let dest = dir.join(name);
    fs::copy(from, &dest).map_err(|e| format!("store {}: {e}", dest.display()))?;
    let (h, size) = store::sha256_file(&dest)?;
    Ok((format!("{}/{version}/{name}", n.id()), h, size))
}

fn frec(role: &str, placed: (String, String, u64), offset: Option<&str>) -> FileRec {
    FileRec {
        role: role.into(),
        path: placed.0,
        sha256: placed.1,
        size: placed.2,
        offset: offset.map(String::from),
    }
}

struct Common<'a> {
    n: Network,
    rel: &'a Release,
    asset: &'a Asset,
    fetched: &'a Fetched,
}

fn mk_entry(c: &Common, b: &Board, variant: &str, method: &str, files: Vec<FileRec>) -> Entry {
    Entry {
        id: entry_id(c.n, b.id, &c.rel.version, variant),
        board: b.id.into(),
        network: c.n.id().into(),
        version: c.rel.version.clone(),
        variant: variant.into(),
        method: method.into(),
        chip: b.chip.into(),
        files,
        licence_id: c.n.licence_id().into(),
        licence_url: c.n.licence_url().into(),
        source: c.asset.url.clone(),
        source_sha256: c.fetched.sha256.clone(),
        fetched_at: timefmt::now_rfc3339(),
        verification: c.fetched.verification.into(),
        unsigned_by_publisher: c.fetched.unsigned,
    }
}

fn add(man: &mut Manifest, e: Entry) {
    man.entries.retain(|o| o.id != e.id);
    man.entries.push(e);
}

#[allow(clippy::too_many_arguments)]
fn build_release(
    p: &Paths,
    f: &dyn Fetcher,
    st: &mut State,
    n: Network,
    rel: &Release,
    boards: &[&Board],
    man: &mut Manifest,
    rep: &mut NetReport,
) {
    match n {
        Network::Meshtastic => build_meshtastic(p, f, st, rel, boards, man, rep),
        Network::Meshcore => build_meshcore(p, f, st, rel, boards, man, rep),
        Network::Rnode => build_rnode(p, f, st, rel, boards, man, rep),
    }
}

fn note(rep: &mut NetReport, id: &str) {
    rep.fetched.push(id.into());
}

// ---- Meshtastic ---------------------------------------------------------------------------

fn read_member(z: &mut zip::ZipArchive<File>, name: &str) -> Result<Option<Vec<u8>>, String> {
    let mut m = match z.by_name(name) {
        Ok(m) => m,
        Err(zip::result::ZipError::FileNotFound) => return Ok(None),
        Err(e) => return Err(format!("{name}: {e}")),
    };
    let mut v = vec![];
    m.by_ref()
        .take(256 << 20)
        .read_to_end(&mut v)
        .map_err(|e| format!("{name}: {e}"))?;
    Ok(Some(v))
}

fn build_meshtastic(
    p: &Paths,
    f: &dyn Fetcher,
    st: &mut State,
    rel: &Release,
    boards: &[&Board],
    man: &mut Manifest,
    rep: &mut NetReport,
) {
    let n = Network::Meshtastic;
    let mut by_chip: BTreeMap<&str, Vec<&Board>> = BTreeMap::new();
    for b in boards {
        let id = entry_id(n, b.id, &rel.version, "");
        if have(p, man, &id) || st.skipped(&id) {
            rep.skipped += 1;
        } else {
            by_chip.entry(b.chip).or_default().push(b);
        }
    }
    for (chip, bs) in by_chip {
        let zname = format!("firmware-{chip}-{}.zip", rel.version);
        let Some(asset) = sources::find(rel, &zname) else {
            rep.errors
                .push(format!("{}: release has no {zname}", rel.tag));
            continue;
        };
        let fetched = match fetch_asset(p, f, asset, None) {
            Ok(x) => x,
            Err(e) => {
                for b in &bs {
                    let id = entry_id(n, b.id, &rel.version, "");
                    st.fail(rep, &id, &rel.tag, e.clone());
                }
                continue;
            }
        };
        let res = (|| -> Result<(), String> {
            let mut z = zip::ZipArchive::new(File::open(&fetched.path).map_err(|e| e.to_string())?)
                .map_err(|e| format!("{zname}: {e}"))?;
            let c = Common {
                n,
                rel,
                asset,
                fetched: &fetched,
            };
            for b in bs {
                match extract_meshtastic(p, &mut z, &c, b) {
                    Ok(e) => {
                        note(rep, &e.id);
                        add(man, e);
                    }
                    Err(e) => st.fail(rep, &entry_id(n, b.id, &rel.version, ""), &rel.tag, e),
                }
            }
            Ok(())
        })();
        let _ = fs::remove_file(&fetched.path);
        if let Err(e) = res {
            rep.errors.push(e);
        }
    }
}

fn part_offset(mt: &Value, subtype: &str) -> Option<String> {
    mt["part"]
        .as_array()?
        .iter()
        .find(|x| x["subtype"] == subtype)?["offset"]
        .as_str()
        .map(String::from)
}

fn extract_meshtastic(
    p: &Paths,
    z: &mut zip::ZipArchive<File>,
    c: &Common,
    b: &Board,
) -> Result<Entry, String> {
    let slug = b.meshtastic.ok_or("no Meshtastic build")?;
    let v = &c.rel.version;
    let need = |z: &mut zip::ZipArchive<File>, name: &str| {
        read_member(z, name)?.ok_or_else(|| format!("{name} is not in the archive"))
    };
    let mut files = vec![];
    let method;
    match b.family {
        Family::Esp32 => {
            method = "esptool";
            let mt_name = format!("firmware-{slug}-{v}.mt.json");
            let Some(mt_bytes) = read_member(z, &mt_name)? else {
                return Err(format!(
                    "unsupported: {v} predates the release flash map ({mt_name}); not offered for ESP32 boards"
                ));
            };
            let mt: Value =
                serde_json::from_slice(&mt_bytes).map_err(|e| format!("mt.json: {e}"))?;
            let mcu = mt["mcu"].as_str().unwrap_or(b.chip).to_string();
            let name = format!("firmware-{slug}-{v}.factory.bin");
            files.push(frec(
                "factory",
                place_bytes(p, c.n, v, &name, &need(z, &name)?)?,
                Some("0x0"),
            ));
            if let Some(off) = part_offset(&mt, "ota_1") {
                let name = format!("mt-{mcu}-ota.bin");
                files.push(frec(
                    "ota",
                    place_bytes(p, c.n, v, &name, &need(z, &name)?)?,
                    Some(&off),
                ));
            }
            if let Some(off) = part_offset(&mt, "spiffs") {
                let name = format!("littlefs-{slug}-{v}.bin");
                files.push(frec(
                    "littlefs",
                    place_bytes(p, c.n, v, &name, &need(z, &name)?)?,
                    Some(&off),
                ));
            }
        }
        Family::Nrf52 => {
            method = "uf2";
            let name = format!("firmware-{slug}-{v}.uf2");
            files.push(frec(
                "uf2",
                place_bytes(p, c.n, v, &name, &need(z, &name)?)?,
                None,
            ));
            let ota = format!("firmware-{slug}-{v}-ota.zip");
            if let Some(d) = read_member(z, &ota)? {
                files.push(frec("dfu-package", place_bytes(p, c.n, v, &ota, &d)?, None));
            }
        }
    }
    Ok(mk_entry(c, b, "", method, files))
}

// ---- MeshCore -----------------------------------------------------------------------------

fn build_meshcore(
    p: &Paths,
    f: &dyn Fetcher,
    st: &mut State,
    rel: &Release,
    boards: &[&Board],
    man: &mut Manifest,
    rep: &mut NetReport,
) {
    let n = Network::Meshcore;
    for b in boards {
        let Some(prefix) = b.meshcore else { continue };
        for variant in ["ble", "usb"] {
            let id = entry_id(n, b.id, &rel.version, variant);
            if have(p, man, &id) || st.skipped(&id) {
                rep.skipped += 1;
                continue;
            }
            let (main_suffix, method) = match b.family {
                Family::Esp32 => ("-merged.bin", "esptool"),
                Family::Nrf52 => (".uf2", "uf2"),
            };
            let Some(asset) = sources::meshcore_asset(rel, prefix, variant, main_suffix) else {
                continue; // not every board has both variants
            };
            let r = (|| -> Result<Entry, String> {
                let fetched = fetch_asset(p, f, asset, None)?;
                let placed = place_file(p, n, &rel.version, &asset.name, &fetched.path);
                let _ = fs::remove_file(&fetched.path);
                let (role, off) = match b.family {
                    Family::Esp32 => ("factory", Some("0x0")),
                    Family::Nrf52 => ("uf2", None),
                };
                let mut files = vec![frec(role, placed?, off)];
                if b.family == Family::Nrf52 {
                    if let Some(z) = sources::meshcore_asset(rel, prefix, variant, ".zip") {
                        let zf = fetch_asset(p, f, z, None)?;
                        let pl = place_file(p, n, &rel.version, &z.name, &zf.path);
                        let _ = fs::remove_file(&zf.path);
                        files.push(frec("dfu-package", pl?, None));
                    }
                }
                let c = Common {
                    n,
                    rel,
                    asset,
                    fetched: &fetched,
                };
                Ok(mk_entry(&c, b, variant, method, files))
            })();
            match r {
                Ok(e) => {
                    note(rep, &e.id);
                    add(man, e);
                }
                Err(e) => st.fail(rep, &id, &rel.tag, e),
            }
        }
    }
}

// ---- RNode --------------------------------------------------------------------------------

fn build_rnode(
    p: &Paths,
    f: &dyn Fetcher,
    st: &mut State,
    rel: &Release,
    boards: &[&Board],
    man: &mut Manifest,
    rep: &mut NetReport,
) {
    let n = Network::Rnode;
    let todo: Vec<&&Board> = boards
        .iter()
        .filter(|b| {
            let id = entry_id(n, b.id, &rel.version, "");
            let skip = have(p, man, &id) || st.skipped(&id);
            if skip {
                rep.skipped += 1;
            }
            !skip
        })
        .collect();
    if todo.is_empty() {
        return;
    }
    // The publisher's own hash list for every zip in the release.
    let hashes: Option<Value> = sources::find(rel, "release.json").and_then(|a| {
        let t = p.tmp().join(format!("rj-{}", std::process::id()));
        let r = f.download(&a.url, &t).ok().and_then(|_| fs::read(&t).ok());
        let _ = fs::remove_file(&t);
        r.and_then(|b| serde_json::from_slice(&b).ok())
    });
    if hashes.is_none() {
        rep.notes.push(format!(
            "{}: no readable release.json, so the publisher checksums could not be used; the release host digest was checked instead",
            rel.tag
        ));
    }
    for b in todo {
        let Some(slug) = b.rnode else { continue };
        let zname = format!("rnode_firmware_{slug}.zip");
        let Some(asset) = sources::find(rel, &zname) else {
            rep.errors
                .push(format!("{}: release has no {zname}", rel.tag));
            continue;
        };
        let publisher = hashes.as_ref().and_then(|h| h[&zname]["hash"].as_str());
        let r = (|| -> Result<Entry, String> {
            let fetched = fetch_asset(p, f, asset, publisher)?;
            let c = Common {
                n,
                rel,
                asset,
                fetched: &fetched,
            };
            let res = (|| -> Result<Entry, String> {
                if b.family == Family::Nrf52 {
                    let pl = place_file(p, n, &rel.version, &zname, &fetched.path)?;
                    return Ok(mk_entry(
                        &c,
                        b,
                        "",
                        "nrfutil",
                        vec![frec("dfu-package", pl, None)],
                    ));
                }
                let mut z =
                    zip::ZipArchive::new(File::open(&fetched.path).map_err(|e| e.to_string())?)
                        .map_err(|e| format!("{zname}: {e}"))?;
                let boot_off = if b.chip == "esp32s3" { "0x0" } else { "0x1000" };
                let parts: [(&str, String, &str, bool); 5] = [
                    (
                        "boot_app0",
                        format!("rnode_firmware_{slug}.boot_app0"),
                        "0xe000",
                        true,
                    ),
                    (
                        "bootloader",
                        format!("rnode_firmware_{slug}.bootloader"),
                        boot_off,
                        true,
                    ),
                    (
                        "partitions",
                        format!("rnode_firmware_{slug}.partitions"),
                        "0x8000",
                        true,
                    ),
                    (
                        "firmware",
                        format!("rnode_firmware_{slug}.bin"),
                        "0x10000",
                        true,
                    ),
                    (
                        "console",
                        "console_image.bin".to_string(),
                        "0x210000",
                        false,
                    ),
                ];
                let mut files = vec![];
                for (role, member, off, required) in parts {
                    match read_member(&mut z, &member)? {
                        Some(d) => {
                            let fname = if role == "console" {
                                format!("rnode_{slug}_console_image.bin")
                            } else {
                                member
                            };
                            files.push(frec(
                                role,
                                place_bytes(p, n, &rel.version, &fname, &d)?,
                                Some(off),
                            ));
                        }
                        None if required => return Err(format!("{member} is not in {zname}")),
                        None => {}
                    }
                }
                Ok(mk_entry(&c, b, "", "esptool", files))
            })();
            let _ = fs::remove_file(&fetched.path);
            res
        })();
        match r {
            Ok(e) => {
                note(rep, &e.id);
                add(man, e);
            }
            Err(e) => st.fail(rep, &entry_id(n, b.id, &rel.version, ""), &rel.tag, e),
        }
    }
}

// ---- keeping two versions ----------------------------------------------------------------

fn prune(
    p: &Paths,
    n: Network,
    rels: &[Release],
    boards: &[&Board],
    man: &mut Manifest,
    rep: &mut NetReport,
) {
    let keep: Vec<&str> = rels.iter().map(|r| r.version.as_str()).collect();
    if keep.is_empty() {
        return;
    }
    let mut drop_ids = vec![];
    for b in boards {
        // Only prune a board once its newest stable is safely in the library.
        let newest_ok = man
            .entries
            .iter()
            .any(|e| e.network == n.id() && e.board == b.id && e.version == keep[0]);
        if !newest_ok {
            continue;
        }
        for e in &man.entries {
            if e.network == n.id() && e.board == b.id && !keep.contains(&e.version.as_str()) {
                drop_ids.push(e.id.clone());
            }
        }
    }
    for id in drop_ids {
        if let Some(pos) = man.entries.iter().position(|e| e.id == id) {
            let e = man.entries.remove(pos);
            for fr in &e.files {
                let still_used = man
                    .entries
                    .iter()
                    .any(|o| o.files.iter().any(|x| x.path == fr.path));
                if !still_used {
                    if let Err(err) = fs::remove_file(p.library().join(&fr.path)) {
                        if err.kind() != std::io::ErrorKind::NotFound {
                            rep.errors.push(format!("remove old {}: {err}", fr.path));
                        }
                    }
                }
            }
        }
    }
}
