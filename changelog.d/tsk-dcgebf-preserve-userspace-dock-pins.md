### Fixed
- Dock restore no longer drops pinned ids for apps not yet registered at restore time. Userspace (.taosapp) pins are preserved in the dock store and re-render once `syncUserspaceApps` registers the app, preventing silent permanent loss of dock pins when the userspace-app fetch races the dock GET.
