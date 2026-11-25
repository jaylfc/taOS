### Added

- native desktop install (and the update path) ships /etc/udev/rules.d/71-taos-mesh-radios.rules (mode 0644, removed on uninstall) tagging common LoRa mesh radio USB serial devices with uaccess -- Espressif native USB (303a), Silicon Labs CP210x (10c4:ea60), WCH CH340/CH9102 (1a86), Adafruit/nRF52 UF2 (239a), Seeed (2886), Nordic (1915) -- so the signed-in person can open them without the dialout group; sorts before 73-seat-late, udev reloaded on install. The same rule ships for the phone under packaging/postmarketos (taOSmobile seat).
