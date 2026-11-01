### Changed

- The desktop shell now uses Inter, self-hosted, so every device renders the same typeface instead of whatever its system font is (Noto Sans on the handset).
- The mobile wallpapers (graphite dark and light, and the original blue) no longer carry the image generator's mark in the bottom-right corner.
- First-run setup is rebuilt as one question per screen over the wallpaper: welcome, your name (the sign-in username follows it until you edit it), a password (8 characters, as the server requires), then your taOS account: sign in with an existing one, create one, or use taOS offline only and sign in later from Settings, Account.
- Setup resumes where it left off after a reload or reboot instead of dropping the owner on a half-set-up desktop. Existing installs are never sent back into setup.
- On a large display, setup sits in a centred glass card with its button inside, instead of a column with the button pinned to the bottom edge of the screen. Buttons show a spinner while working, and when new taOS accounts are closed, setup offers Continue offline instead of a dead end.
- A fresh install now opens straight into the new setup screens instead of the older plain setup form, which stays available for browsers without JavaScript.
- On the device's own screen, setup's password step also offers an optional 4 to 12 digit PIN for unlocking that screen, as the older setup form did. A PIN that is too short says so instead of just greying out Continue. Other devices are never offered it, and the server still refuses a PIN from them.
- After signing in to a taOS account, setup says this is your first taOS device. Without an account that screen is left for later, in the Setup checklist.
