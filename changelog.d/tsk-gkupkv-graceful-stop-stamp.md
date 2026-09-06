### Security

- The graceful-shutdown hook no longer keeps its dedupe stamp in a world-writable directory: any local user could plant that file and silently stop taOS draining agents on every restart and reboot. The stamp now lives in the service's own runtime directory (`/run/taos`, mode 0750, or `data/` on installs without systemd), carries its own timestamp so the age check works on macOS and BSD too, and the drain runs rather than being skipped if no private location is available.
- Fixed the stamp age check suppressing the drain indefinitely when the recorded epoch is in the future (RTC-less Pis routinely step the clock forward after an NTP sync post power-cut): the age is now floored at zero before the dedupe window is applied.
