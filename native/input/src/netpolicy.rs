// SPDX-License-Identifier: AGPL-3.0-or-later
//! Who may connect, and where the engine may listen.
//!
//! The input channel is a remote control for the desktop, so the rules are
//! strict and live in one place: the engine only ever binds one explicit
//! private address (never the wildcard, never a public address), and only
//! accepts peers from the allowed networks (private ranges by default).

use std::net::{IpAddr, SocketAddr, UdpSocket};

/// One network in CIDR form.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Net {
    addr: IpAddr,
    prefix: u8,
}

impl Net {
    pub fn parse(s: &str) -> Result<Net, String> {
        let (a, p) = match s.split_once('/') {
            Some((a, p)) => (a, Some(p)),
            None => (s, None),
        };
        let addr: IpAddr = a.parse().map_err(|_| format!("bad network address: {s}"))?;
        let max = if addr.is_ipv4() { 32 } else { 128 };
        let prefix = match p {
            Some(p) => p.parse::<u8>().map_err(|_| format!("bad prefix: {s}"))?,
            None => max,
        };
        if prefix > max {
            return Err(format!("prefix too long: {s}"));
        }
        Ok(Net { addr, prefix })
    }

    pub fn contains(&self, ip: IpAddr) -> bool {
        match (self.addr, normalise(ip)) {
            (IpAddr::V4(n), IpAddr::V4(i)) => mask_eq(&n.octets(), &i.octets(), self.prefix),
            (IpAddr::V6(n), IpAddr::V6(i)) => mask_eq(&n.octets(), &i.octets(), self.prefix),
            _ => false,
        }
    }
}

fn mask_eq(a: &[u8], b: &[u8], prefix: u8) -> bool {
    let full = (prefix / 8) as usize;
    let rest = prefix % 8;
    if a[..full] != b[..full] {
        return false;
    }
    if rest == 0 {
        return true;
    }
    let m = 0xffu8 << (8 - rest);
    (a[full] & m) == (b[full] & m)
}

/// An IPv4-mapped IPv6 address is the IPv4 address it wraps.
fn normalise(ip: IpAddr) -> IpAddr {
    match ip {
        IpAddr::V6(v6) => match v6.to_ipv4_mapped() {
            Some(v4) => IpAddr::V4(v4),
            None => IpAddr::V6(v6),
        },
        v4 => v4,
    }
}

/// The default allowed networks: RFC 1918, link-local, and IPv6 unique-local
/// and link-local. Loopback and the tailnet range are NOT in the list; they
/// have to be named explicitly.
pub fn default_lan() -> Vec<Net> {
    [
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",
        "fc00::/7",
        "fe80::/10",
    ]
    .iter()
    .map(|s| Net::parse(s).expect("static network"))
    .collect()
}

#[derive(Clone, Debug)]
pub struct Policy {
    allowed: Vec<Net>,
}

impl Policy {
    pub fn new(allowed: Vec<Net>) -> Policy {
        Policy { allowed }
    }

    pub fn lan() -> Policy {
        Policy::new(default_lan())
    }

    /// May a peer at this address talk to the engine at all?
    pub fn allows_peer(&self, ip: IpAddr) -> bool {
        self.allowed.iter().any(|n| n.contains(ip))
    }

    /// May the engine listen on this address? It must be one specific address
    /// that the policy itself allows: so the wildcard, multicast and any
    /// public address are refused.
    pub fn check_bind(&self, ip: IpAddr) -> Result<(), String> {
        if ip.is_unspecified() {
            return Err("refusing to listen on the wildcard address; name one LAN address".into());
        }
        if ip.is_multicast() {
            return Err("refusing to listen on a multicast address".into());
        }
        if !self.allows_peer(ip) {
            return Err(format!(
                "refusing to listen on {ip}: it is not inside an allowed network (home networks only)"
            ));
        }
        Ok(())
    }
}

/// The address the machine uses to reach the home network: the source address
/// the kernel would pick for a private destination. Sends nothing.
pub fn detect_lan_addr(policy: &Policy) -> Option<IpAddr> {
    for probe in ["192.168.255.254:9", "10.255.255.254:9", "172.31.255.254:9"] {
        let Ok(sock) = UdpSocket::bind("0.0.0.0:0") else {
            continue;
        };
        if sock.connect(probe).is_err() {
            continue;
        }
        if let Ok(SocketAddr::V4(a)) = sock.local_addr() {
            let ip = IpAddr::V4(*a.ip());
            if policy.check_bind(ip).is_ok() {
                return Some(ip);
            }
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ip(s: &str) -> IpAddr {
        s.parse().unwrap()
    }

    #[test]
    fn private_ranges_are_lan_and_public_is_not() {
        let p = Policy::lan();
        for ok in [
            "10.1.2.3",
            "172.16.0.1",
            "172.31.255.255",
            "192.168.1.9",
            "169.254.3.4",
            "fd12::1",
            "fe80::1",
        ] {
            assert!(p.allows_peer(ip(ok)), "{ok} should be LAN");
        }
        for bad in [
            "8.8.8.8",
            "172.32.0.1",
            "172.15.255.255",
            "100.64.0.1",
            "127.0.0.1",
            "::1",
            "2001:db8::1",
            "192.169.0.1",
        ] {
            assert!(!p.allows_peer(ip(bad)), "{bad} must not be LAN");
        }
    }

    #[test]
    fn ipv4_mapped_peer_is_judged_as_its_ipv4_address() {
        let p = Policy::lan();
        assert!(p.allows_peer(ip("::ffff:192.168.1.5")));
        assert!(!p.allows_peer(ip("::ffff:8.8.8.8")));
    }

    #[test]
    fn bind_refuses_wildcard_multicast_and_public() {
        let p = Policy::lan();
        assert!(p.check_bind(ip("0.0.0.0")).is_err());
        assert!(p.check_bind(ip("::")).is_err());
        assert!(p.check_bind(ip("224.0.0.1")).is_err());
        assert!(p.check_bind(ip("93.184.216.34")).is_err());
        assert!(p.check_bind(ip("192.168.1.20")).is_ok());
    }

    #[test]
    fn loopback_needs_an_explicit_network() {
        assert!(Policy::lan().check_bind(ip("127.0.0.1")).is_err());
        let p = Policy::new(vec![Net::parse("127.0.0.1/32").unwrap()]);
        assert!(p.check_bind(ip("127.0.0.1")).is_ok());
        assert!(!p.allows_peer(ip("127.0.0.2")));
    }

    #[test]
    fn parse_rejects_nonsense() {
        assert!(Net::parse("10.0.0.0/33").is_err());
        assert!(Net::parse("banana").is_err());
        assert!(Net::parse("fc00::/129").is_err());
        assert_eq!(Net::parse("10.0.0.1").unwrap().prefix, 32);
    }
}
