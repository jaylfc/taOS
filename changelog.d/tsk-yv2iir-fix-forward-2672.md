### Fixed
- Pin container provisioning to incus/LXC backend; the global backend
  resolution previously selected Docker on docker-capable hosts, which
  broke provisioning because the card scopes creation to incus/LXC only.
- Move non-approve request creation inside the policy-check lock so
  concurrent over-threshold requests cannot both insert and escalate
  outside the atomicity guard.
- Sanitize canonical_id when composing the container name so invalid
  characters cannot leak into incus instance names.
- Propagate create_container, set_env, and destroy_container failures
  instead of swallowing them silently.
