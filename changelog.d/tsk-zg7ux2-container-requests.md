### Added

- Agent container provisioning request API: `POST /api/containers/requests`
  lets an active agent submit a provisioning request using its own registry JWT.
  A `ContainerRequestStore` (`tinyagentos/container_requests_store.py`) tracks the
  request state machine (requested, approved, pending-approval, provisioned,
  failed), and a `ProvisioningPolicy` (`tinyagentos/containers/provisioning_policy.py`)
  evaluates per-agent quota and threshold from `container_provisioning` config:
  under quota auto-approves, over quota lands in pending-approval, and over
  threshold escalates to a Decisions-app item for Jay. No provisioning happens in
  this slice (P1); the executor lands in P2.
