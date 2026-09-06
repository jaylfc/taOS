### Security

- Agent access requests and scope requests now enforce their per-identity pending cap atomically, inside the insert. A burst of concurrent requests could previously all read the same pre-insert count, all pass the check and all be stored, letting one agent flood the approver's queue past the cap that exists to stop it.
