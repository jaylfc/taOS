// SPDX-License-Identifier: AGPL-3.0-or-later
//! The supported boards, networks and their naming in each project's releases.
//!
//! USB ids and UF2 board-id strings are written from public documentation and are NOT yet checked
//! against hardware: detection only ever proposes candidates, and flashing still needs the person
//! to type the board name.
use serde::Serialize;

#[derive(Clone, Copy, PartialEq, Eq, Debug, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Family {
    Esp32,
    Nrf52,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug, Serialize, Hash, PartialOrd, Ord)]
#[serde(rename_all = "lowercase")]
pub enum Network {
    Meshtastic,
    Meshcore,
    Rnode,
}

pub const NETWORKS: [Network; 3] = [Network::Meshtastic, Network::Meshcore, Network::Rnode];

impl Network {
    pub fn id(self) -> &'static str {
        match self {
            Network::Meshtastic => "meshtastic",
            Network::Meshcore => "meshcore",
            Network::Rnode => "rnode",
        }
    }
    pub fn parse(s: &str) -> Option<Network> {
        NETWORKS.iter().copied().find(|n| n.id() == s)
    }
    pub fn display(self) -> &'static str {
        match self {
            Network::Meshtastic => "Meshtastic",
            Network::Meshcore => "MeshCore",
            Network::Rnode => "RNode (Reticulum)",
        }
    }
    /// GitHub `owner/repo` the releases come from.
    pub fn repo(self) -> &'static str {
        match self {
            Network::Meshtastic => "meshtastic/firmware",
            Network::Meshcore => "meshcore-dev/MeshCore",
            Network::Rnode => "markqvist/RNode_Firmware",
        }
    }
    pub fn licence_id(self) -> &'static str {
        match self {
            Network::Meshtastic | Network::Rnode => "GPL-3.0-only",
            Network::Meshcore => "MIT",
        }
    }
    pub fn licence_url(self) -> &'static str {
        match self {
            Network::Meshtastic => "https://github.com/meshtastic/firmware/blob/master/LICENSE",
            Network::Meshcore => "https://github.com/meshcore-dev/MeshCore/blob/main/license.txt",
            Network::Rnode => "https://github.com/markqvist/RNode_Firmware/blob/master/LICENSE",
        }
    }
}

pub struct Board {
    pub id: &'static str,
    /// The name the person types to confirm a flash.
    pub name: &'static str,
    pub family: Family,
    /// esptool chip name (`esp32`, `esp32s3`) or `nrf52840`.
    pub chip: &'static str,
    /// USB (vendor, product) pairs seen on the serial port in normal and bootloader mode.
    pub usb: &'static [(u16, u16)],
    /// Lower-case substrings of the UF2 `Board-ID` / `Model` lines.
    pub uf2: &'static [&'static str],
    /// Board slug in Meshtastic assets (`firmware-<slug>-...`).
    pub meshtastic: Option<&'static str>,
    /// Asset prefix in MeshCore companion releases (`<prefix>_companion_radio_<ble|usb>-...`).
    pub meshcore: Option<&'static str>,
    /// Slug in RNode release assets (`rnode_firmware_<slug>.zip`).
    pub rnode: Option<&'static str>,
    pub recovery: &'static str,
}

const ESP_NATIVE: (u16, u16) = (0x303a, 0x1001);
const CP210X: (u16, u16) = (0x10c4, 0xea60);

pub const BOARDS: &[Board] = &[
    Board {
        id: "heltec-v3",
        name: "Heltec V3",
        family: Family::Esp32,
        chip: "esp32s3",
        usb: &[CP210X],
        uf2: &[],
        meshtastic: Some("heltec-v3"),
        meshcore: Some("Heltec_v3"),
        rnode: Some("heltec32v3"),
        recovery: "Hold the PRG (BOOT) button, tap RST, then release PRG to enter the ROM bootloader. \
Unplug and replug the USB cable, then run the flash again. If the port does not appear, try another cable (many are charge-only).",
    },
    Board {
        id: "heltec-v4",
        name: "Heltec V4",
        family: Family::Esp32,
        chip: "esp32s3",
        usb: &[ESP_NATIVE],
        uf2: &[],
        meshtastic: Some("heltec-v4"),
        meshcore: Some("heltec_v4"),
        rnode: Some("heltec32v4pa"),
        recovery: "Hold the PRG (BOOT) button, tap RST, then release PRG to enter the ROM bootloader. \
Unplug and replug the USB cable, then run the flash again.",
    },
    Board {
        id: "tbeam",
        name: "LilyGO T-Beam",
        family: Family::Esp32,
        chip: "esp32",
        usb: &[CP210X],
        uf2: &[],
        meshtastic: Some("tbeam"),
        meshcore: Some("Tbeam_SX1262"),
        rnode: Some("tbeam_sx1262"),
        recovery: "Switch the board off, hold the BOOT (IO0) button, switch it on or plug USB in, then release BOOT. \
Run the flash again. Check the radio variant (SX1262 or SX1276) printed on the board.",
    },
    Board {
        id: "tbeam-s3",
        name: "LilyGO T-Beam Supreme",
        family: Family::Esp32,
        chip: "esp32s3",
        usb: &[ESP_NATIVE],
        uf2: &[],
        meshtastic: Some("tbeam-s3-core"),
        meshcore: Some("T_Beam_S3_Supreme_SX1262"),
        rnode: Some("tbeam_supreme"),
        recovery: "Hold the BOOT button, tap RST (or plug in USB while holding BOOT), then release BOOT. \
Unplug and replug USB, then run the flash again.",
    },
    Board {
        id: "rak4631",
        name: "RAK4631",
        family: Family::Nrf52,
        chip: "nrf52840",
        usb: &[(0x239a, 0x8029), (0x239a, 0x0029)],
        uf2: &["rak4631"],
        meshtastic: Some("rak4631"),
        meshcore: Some("RAK_4631"),
        rnode: Some("rak4631"),
        recovery: "Double-tap the RESET button on the WisBlock core quickly. A drive named WISBOOT (or similar) \
appears and the green LED pulses. Copy the .uf2 file onto it. If the drive never appears the bootloader is damaged and needs a SWD programmer.",
    },
    Board {
        id: "t1000e",
        name: "Seeed T1000-E",
        family: Family::Nrf52,
        chip: "nrf52840",
        usb: &[(0x2886, 0x0057), (0x2886, 0x8057)],
        uf2: &["t1000", "sensecap"],
        meshtastic: Some("tracker-t1000-e"),
        meshcore: Some("t1000e"),
        rnode: None,
        recovery: "Plug in USB, then double-press the button quickly (or press the button while plugging in). \
A drive named T1000-E appears. Copy the .uf2 file onto it.",
    },
    Board {
        id: "xiao-nrf52840",
        name: "Seeed XIAO nRF52840",
        family: Family::Nrf52,
        chip: "nrf52840",
        usb: &[(0x2886, 0x0045), (0x2886, 0x8045)],
        uf2: &["xiao"],
        meshtastic: Some("seeed_xiao_nrf52840_kit"),
        meshcore: Some("Xiao_nrf52"),
        rnode: None,
        recovery: "Double-tap the small RESET button on the XIAO quickly. A drive named XIAO-SENSE (or XIAO-BOOT) appears. \
Copy the .uf2 file onto it.",
    },
];

pub fn board(id: &str) -> Option<&'static Board> {
    BOARDS.iter().find(|b| b.id == id)
}

impl Board {
    pub fn supports(&self, n: Network) -> bool {
        match n {
            Network::Meshtastic => self.meshtastic.is_some(),
            Network::Meshcore => self.meshcore.is_some(),
            Network::Rnode => self.rnode.is_some(),
        }
    }
    pub fn networks(&self) -> Vec<Network> {
        NETWORKS
            .iter()
            .copied()
            .filter(|n| self.supports(*n))
            .collect()
    }
}

pub const GENERIC_RECOVERY: &str = "Unplug the board and replug it. If it no longer shows as a serial port or drive, \
put it in its bootloader (see the board recovery steps) and run the flash again. A board is very rarely bricked: \
the ROM bootloader on ESP32 and the UF2 bootloader on nRF52 survive a failed flash.";

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn ids_unique_and_named() {
        for (i, b) in BOARDS.iter().enumerate() {
            assert!(BOARDS.iter().skip(i + 1).all(|o| o.id != b.id));
            assert!(!b.recovery.is_empty() && !b.name.is_empty());
        }
    }
}
