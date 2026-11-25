### Added

- Native desktop: one taOS sign-in for every native app. Every app that finds no usable desktop session (nobody signed in this login, or the controller refused the stored one) now offers the same small sign-in; signing in once, in any app, signs in all of them. The Store shows "Signed out" with a Sign in button beside the saved catalogue instead of "Not connected", and "Cannot reach the controller" when the controller is down (a different problem with a different fix). New `taos-session status | login | logout` command line tool (password on stdin only, the session is never printed) so a person or an agent can sign the desktop in and out; `logout` also revokes the session at the controller.

### Fixed

- Native desktop: the shared session file is only trusted when its directory is a plain directory owned by the person with no group or other access (a symlinked or loosened directory is refused).
