## Added

- Fixed S2-24 vulnerability in `_worker_for_resource` where a worker could fabricate
  unlimited 24h leases by supplying resource IDs not validated against registered
  capabilities.

## Details

**Problem:** The `_worker_for_resource` method in `tinyagentos/cluster/manager.py` only validated
that a worker exists and is online, but didn't verify that the resource half of a
lease key (`resource_id`) matched one of the worker's registered backends. This
allowed compromised or buggy workers to claim leases on fabricated resources,
creating unbounded lease entries with 24h TTLs that consumed controller memory/DB
space.

**Solution:** Added validation in `_worker_for_resource` to ensure the resource
name (the part after the colon in `worker-name:resource-name`) matches a backend
name registered to the worker. A worker with N registered backends can only
claim leases for those N resources.

**Impact:** Prevents unlimited lease creation attacks, caps leases per worker,
reduces controller memory/DB growth, and clarifies lease state.

**Files changed:**
- `tinyagentos/cluster/manager.py:528-542` - Added resource validation
- `tests/test_s2_24_unvalidated_resource_leases.py` - Added RED test for acceptance

**Acceptance:** Test `test_vulnerability_exposed_before_fix` verifies that:
1. Claims on fabricated resources are rejected (worker can't claim unlimited leases)
2. Claims on valid resources are still accepted (worker can use registered backends)
3. Count of leases for the worker stays at registered number (max 2 for test)
