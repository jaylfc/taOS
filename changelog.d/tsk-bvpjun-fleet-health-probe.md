### Fixed
- `fleet_health.sh` BOARD STRANGLED probe now captures `next_card.py`'s exit code and stderr instead of discarding them; probe failures and timeouts are reported as `probe failed:` and no longer recommend pruning. The alarm requires corroboration across two consecutive empty probes and is suppressed when the claimable count is falling.
