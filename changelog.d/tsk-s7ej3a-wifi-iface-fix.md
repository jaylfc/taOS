### Fixed

- wifi_connect now passes the chosen device's interface name to the profile-reuse filter even when the request body omits `iface`, preventing reuse of a profile pinned to a different interface