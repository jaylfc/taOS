### Fixed
`checkpoint_and_clear.sh` now enforces the 32768-byte resume rotation limit on
the artefact that ships: the size check runs AFTER the retrospective and
FLEET-HEALTH blocks are appended and trims the file back to the limit when they
push it over, so a checkpoint that passed the pre-check can no longer truncate
on its successor's next Read while its clear is being dispatched.
