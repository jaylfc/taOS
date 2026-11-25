// SPDX-License-Identifier: AGPL-3.0-or-later
//! taos-firmware: firmware library and guarded flasher for LoRa mesh boards.
fn main() {
    std::process::exit(taos_firmware::cli::run(std::env::args().skip(1).collect()));
}
