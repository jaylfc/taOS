### Fixed

- Fixed ActivateConnection D-Bus signature from "soo" to "ooo" to match NetworkManager API (all three arguments are object paths)
- Fixed PSK update to merge into existing profile instead of replacing all settings, preserving ipv4/ipv6 config, connection uuid, and other sections