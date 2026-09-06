### Fixed

- S2-24: Prevent workers from claiming unlimited leases on fabricated resources. The `_worker_for_resource` method now validates that the resource part of a resource_id matches one of the worker's registered backends before accepting the lease claim. Workers are also limited to a maximum of 10 concurrent leases each (configurable via `_max_leases_per_worker`).