### Fixed

- On mobile, the "New taOS build available" info toast no longer stays overlaid on the home pages. The per-toast dismiss timer is now driven by a stable callback reference so mobile shell re-renders no longer reset the 5-second auto-dismiss timeout. The toast still clears from view while the notification remains in the notification centre.
