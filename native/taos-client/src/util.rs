//! Small deserialisation helpers shared by the typed domain modules.
//!
//! The controller declares very few response models, so every response type in
//! this crate is hand-written and deliberately forgiving: unknown fields are
//! ignored, optional fields default, and enums keep unknown values.

use serde::{Deserialize, Deserializer, Serialize, Serializer};

/// Define a string-valued enum that keeps unknown values in `Unknown(String)`.
///
/// The generated type serialises back to the original string, implements
/// `Default` as `Unknown("")` (so `#[serde(default)]` works) and never fails to
/// deserialise a string.
macro_rules! string_enum {
    (
        $(#[$meta:meta])*
        $vis:vis enum $name:ident {
            $( $(#[$vmeta:meta])* $variant:ident => $text:literal ),+ $(,)?
        }
    ) => {
        $(#[$meta])*
        #[derive(Debug, Clone, PartialEq, Eq, Hash)]
        $vis enum $name {
            $( $(#[$vmeta])* $variant, )+
            /// A value this version of the client does not know about.
            Unknown(String),
        }

        impl $name {
            /// The wire string for this value.
            pub fn as_str(&self) -> &str {
                match self {
                    $( Self::$variant => $text, )+
                    Self::Unknown(s) => s.as_str(),
                }
            }
        }

        impl Default for $name {
            fn default() -> Self {
                Self::Unknown(String::new())
            }
        }

        impl<'de> serde::Deserialize<'de> for $name {
            fn deserialize<D: serde::Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
                let s = $crate::util::string_or_default(d)?;
                Ok(match s.as_str() {
                    $( $text => Self::$variant, )+
                    _ => Self::Unknown(s),
                })
            }
        }

        impl serde::Serialize for $name {
            fn serialize<S: serde::Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
                s.serialize_str(self.as_str())
            }
        }
    };
}
pub(crate) use string_enum;

/// Deserialise a string, accepting `null` and bare numbers/booleans.
pub(crate) fn string_or_default<'de, D: Deserializer<'de>>(d: D) -> Result<String, D::Error> {
    Ok(match serde_json::Value::deserialize(d)? {
        serde_json::Value::String(s) => s,
        serde_json::Value::Null => String::new(),
        other => other.to_string(),
    })
}

/// Deserialise `null` as the type's default value.
pub(crate) fn null_default<'de, D, T>(d: D) -> Result<T, D::Error>
where
    D: Deserializer<'de>,
    T: Default + Deserialize<'de>,
{
    Ok(Option::<T>::deserialize(d)?.unwrap_or_default())
}

/// Deserialise a list, treating `null`, an empty object or any non-list value
/// as an empty list. The controller returns `{}` where a list is normal on
/// some error paths (for example `zram` and `network` on `/api/activity`).
pub(crate) fn lenient_vec<'de, D, T>(d: D) -> Result<Vec<T>, D::Error>
where
    D: Deserializer<'de>,
    T: for<'a> Deserialize<'a>,
{
    let v = serde_json::Value::deserialize(d)?;
    match v {
        serde_json::Value::Array(items) => Ok(items
            .into_iter()
            .filter_map(|i| serde_json::from_value(i).ok())
            .collect()),
        _ => Ok(Vec::new()),
    }
}

/// A point in time that the controller may send as epoch seconds or as an ISO
/// 8601 string.
#[derive(Debug, Clone, PartialEq)]
pub enum Timestamp {
    /// Seconds since the Unix epoch (fractional).
    Epoch(f64),
    /// An ISO 8601 / RFC 3339 string, as sent by some stores.
    Iso(String),
}

impl Timestamp {
    /// Epoch seconds. ISO strings are parsed (UTC or numeric offset, optional
    /// fraction); `None` when the string is not a recognisable timestamp.
    pub fn epoch_secs(&self) -> Option<f64> {
        match self {
            Self::Epoch(v) => Some(*v),
            Self::Iso(s) => parse_iso(s),
        }
    }
}

impl<'de> Deserialize<'de> for Timestamp {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        Ok(match serde_json::Value::deserialize(d)? {
            serde_json::Value::Number(n) => Self::Epoch(n.as_f64().unwrap_or(0.0)),
            serde_json::Value::String(s) => match s.parse::<f64>() {
                Ok(v) => Self::Epoch(v),
                Err(_) => Self::Iso(s),
            },
            _ => Self::Epoch(0.0),
        })
    }
}

impl Serialize for Timestamp {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        match self {
            Self::Epoch(v) => s.serialize_f64(*v),
            Self::Iso(v) => s.serialize_str(v),
        }
    }
}

/// Days since 1970-01-01 for a proleptic Gregorian date.
fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

fn parse_iso(s: &str) -> Option<f64> {
    let s = s.trim();
    let (date, rest) = s.split_once(['T', ' '])?;
    let mut dp = date.splitn(3, '-');
    let y: i64 = dp.next()?.parse().ok()?;
    let m: i64 = dp.next()?.parse().ok()?;
    let d: i64 = dp.next()?.parse().ok()?;
    if !(1..=12).contains(&m) || !(1..=31).contains(&d) {
        return None;
    }
    // Split the clock from the zone designator.
    let zone_at = rest.find(['Z', 'z', '+', '-']);
    let (clock, zone) = match zone_at {
        Some(i) => rest.split_at(i),
        None => (rest, ""),
    };
    let mut cp = clock.splitn(3, ':');
    let hh: f64 = cp.next()?.parse().ok()?;
    let mm: f64 = cp.next()?.parse().ok()?;
    let ss: f64 = cp.next().unwrap_or("0").parse().ok()?;
    let offset = match zone {
        "" | "Z" | "z" => 0.0,
        z => {
            let sign = if z.starts_with('-') { -1.0 } else { 1.0 };
            let body = &z[1..];
            let (oh, om) = body.split_once(':').unwrap_or((body, "0"));
            sign * (oh.parse::<f64>().ok()? * 3600.0 + om.parse::<f64>().ok()? * 60.0)
        }
    };
    let days = days_from_civil(y, m, d) as f64;
    Some(days * 86_400.0 + hh * 3600.0 + mm * 60.0 + ss - offset)
}

/// Percent-encode one path segment the way `encodeURIComponent` does.
pub(crate) fn encode_segment(seg: &str) -> String {
    let mut out = String::with_capacity(seg.len());
    for b in seg.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(b as char)
            }
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

/// Encode a relative file path segment by segment, keeping `/` separators.
/// Returns `None` for paths containing `.` or `..` segments.
pub(crate) fn encode_path(path: &str) -> Option<String> {
    let mut parts = Vec::new();
    for seg in path.split('/').filter(|s| !s.is_empty()) {
        if seg == "." || seg == ".." {
            return None;
        }
        parts.push(encode_segment(seg));
    }
    Some(parts.join("/"))
}

/// Lenient number: accepts numbers and numeric strings, anything else is `None`.
pub(crate) fn opt_num<'de, D: Deserializer<'de>>(d: D) -> Result<Option<f64>, D::Error> {
    Ok(match serde_json::Value::deserialize(d)? {
        serde_json::Value::Number(n) => n.as_f64(),
        serde_json::Value::String(s) => s.trim().parse().ok(),
        _ => None,
    })
}

/// Lenient number defaulting to zero.
pub(crate) fn num<'de, D: Deserializer<'de>>(d: D) -> Result<f64, D::Error> {
    Ok(opt_num(d)?.unwrap_or(0.0))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn iso_parses() {
        assert_eq!(parse_iso("1970-01-02T00:00:00Z"), Some(86_400.0));
        assert_eq!(parse_iso("1970-01-01T01:00:00+01:00"), Some(0.0));
        assert_eq!(
            parse_iso("2024-03-01T00:00:00.5+00:00"),
            Some(1_709_251_200.5)
        );
        assert_eq!(parse_iso("nonsense"), None);
    }

    #[test]
    fn encodes_like_encode_uri_component() {
        assert_eq!(encode_segment("a b/ü"), "a%20b%2F%C3%BC");
        assert_eq!(encode_path("x y/z").as_deref(), Some("x%20y/z"));
        assert_eq!(encode_path("a/../b"), None);
    }
}
