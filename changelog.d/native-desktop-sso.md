### Added

- Native desktop: one taOS sign-in for every native app. Every app that finds no usable desktop session (nobody signed in this login, or the controller refused the stored one) now offers the same small sign-in; signing in once, in any app, signs in all of them. The Store shows "Signed out" with a Sign in button beside the saved catalogue instead of "Not connected", and "Cannot reach the controller" when the controller is down (a different problem with a different fix). New `taos-session status | login | logout` command line tool (password on stdin only, the session is never printed) so a person or an agent can sign the desktop in and out; `logout` also revokes the session at the controller.

- Native desktop: sign-in at login with no password. `taos-session sso` (started with the shell session when installed) asks the controller's local sign-in socket for the session of this Linux account (the controller reads the uid from the kernel and maps it through a root-owned link file, controller PR #4558), writes the 0600 session file, and renews it. It never overwrites a password sign-in and never signs a person back in after a sign-out; a sign-out is remembered until the next sign-in. If the account is not linked or there is no socket it exits quietly and the shared sign-in form stays the fallback. Lock keeps the session; Log out in the launcher now runs `taos-session logout` (revokes at the controller, removes the file) before ending the session. New `taos-session acquire` does one request by hand.

### Fixed

- Native desktop: the shared session file is only trusted when its directory is a plain directory owned by the person with no group or other access (a symlinked or loosened directory is refused).
