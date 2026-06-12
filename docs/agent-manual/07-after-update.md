# After an Update

<!-- Check the breakage log first before reasoning from scratch on "it worked before" reports. -->

## After an update (check this FIRST for "it worked before" reports)

The repository keeps a log of every change that can affect existing installs, with symptoms and fixes:

- In the repo: `docs/UPDATE_BREAKAGE_LOG.md`
- Latest: `https://raw.githubusercontent.com/jaylfc/tinyagentos/master/docs/UPDATE_BREAKAGE_LOG.md`

Match the user's symptom against that log before reasoning from scratch. Known classics: apps that grabbed a core port before mid-2026 need a Store reinstall; cluster workers from before pairing need a one-time re-pair (restart the worker, approve the code in Cluster).
