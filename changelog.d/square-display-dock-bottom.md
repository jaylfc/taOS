Fixed: on roughly square displays (such as the Unihertz Titan 2) the mobile dock
sat well above the bottom edge. The fixed 54px browser-chrome reserve is now
reduced to the standard 12px gap on square viewports, where the `100dvh` root
already accounts for browser chrome. Tall phones are unchanged.
