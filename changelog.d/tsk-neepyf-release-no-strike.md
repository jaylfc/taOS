### Fixed

- Release endpoint now accepts an optional `strike` body field (defaults to `true`). When set to `false`, releasing a claimed task clears the claim without recording a dispatch-failed strike or quarantining the card. This lets the executor mark watchdog kills, model hangs, and tooling faults as innocent releases so they no longer accumulate strikes against good cards.
