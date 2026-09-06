from __future__ import annotations

"""Provisioning policy: decides what happens to a container request.

The policy is driven entirely by config (``app.state.config.container_provisioning``):

- ``quota``: max containers an agent may have auto-approved at once.
- ``threshold``: if an agent already has >= threshold non-terminal requests,
  the new request is escalated to a Decisions-app item for Jay instead of
  auto-approving or pending.
- ``per_agent_quota`` / ``per_agent_threshold``: per-agent overrides.

A request is auto-approved when the agent's current non-terminal count is
below the effective quota. When the count sits between the quota and the
threshold, the request lands in ``pending-approval`` (manual review). When
the count meets or exceeds the threshold, the request is escalated to a
Decision and its state is set to ``pending-approval`` so a human can resolve
the Decision and then approve/reject.

The policy engine is intentionally pure: it takes a count and returns a
verdict. The route wires the verdict to the store + DecisionStore.
"""

from dataclasses import dataclass

# Policy verdicts
APPROVE = "approve"
PENDING = "pending-approval"
ESCALATE = "escalate"

_VERDICT_PRIORITY = {APPROVE: 0, PENDING: 1, ESCALATE: 2}


@dataclass
class PolicyConfig:
    quota: int
    threshold: int
    per_agent_quota: dict = None
    per_agent_threshold: dict = None

    def __post_init__(self):
        self.per_agent_quota = self.per_agent_quota or {}
        self.per_agent_threshold = self.per_agent_threshold or {}

    @classmethod
    def from_app_config(cls, config) -> "PolicyConfig":
        cp = getattr(config, "container_provisioning", None)
        if cp is None:
            cp = {}
        return cls(
            quota=int(cp.get("quota", 2)),
            threshold=int(cp.get("threshold", 5)),
            per_agent_quota=dict(cp.get("per_agent_quota", {})),
            per_agent_threshold=dict(cp.get("per_agent_threshold", {})),
        )


class ProvisioningPolicy:
    """Evaluates container requests against quota + threshold rules."""

    def __init__(self, config: PolicyConfig | None = None):
        self._config = config

    def configure(self, config: PolicyConfig) -> None:
        self._config = config

    @property
    def config(self) -> PolicyConfig | None:
        return self._config

    def _effective_limits(self, canonical_id: str) -> tuple[int, int]:
        if self._config is None:
            return 2, 5
        quota = self._config.per_agent_quota.get(canonical_id, self._config.quota)
        threshold = self._config.per_agent_threshold.get(canonical_id, self._config.threshold)
        if threshold < quota:
            threshold = quota
        return quota, threshold

    def evaluate(self, canonical_id: str, active_count: int) -> str:
        """Return the policy verdict for an agent's request given its current
        non-terminal container count."""
        quota, threshold = self._effective_limits(canonical_id)
        if active_count >= threshold:
            return ESCALATE
        if active_count >= quota:
            return PENDING
        return APPROVE
