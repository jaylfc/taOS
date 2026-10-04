### Fixed

- The server installer now pins every controller pip install against a committed `scripts/install-constraints.txt` generated from `uv.lock`, so the dependency set on a production box can no longer drift from the graph the licence gate audits.
