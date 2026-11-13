### Fixed

- Wi-Fi connect (POST /api/hardware/wifi/connect) now reuses an existing NetworkManager connection profile for the requested SSID instead of adding a new one on every connect, stopping duplicate profiles (MyNetwork, MyNetwork 1, ...) from piling up on reconnect.