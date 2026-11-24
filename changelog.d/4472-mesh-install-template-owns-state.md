### Fixed

- The mesh instance owner step `install-template` now takes ownership of an EXISTING state root created earlier by root (an image build, a package, or a first `allocate` run as root) and of its slot table, so `allocate` as the service account no longer fails with a permission error. Person directories keep their owner. A symlinked state root or slot file is refused.
