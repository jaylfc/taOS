### Fixed

- `update_task` can no longer un-park a card: the parked guard now runs before the
  update-candidate list is built, so `update_task(status="open")` on a parked task
  leaves it parked instead of returning it to the ready pool. The guard is also
  race-free — a status edit will not land on a row parked after the guard read.
- Parking a card clears its claim: `park_task` now nulls `claimed_by`/`claimed_at`,
  so a parked card can never show as held by an agent that can no longer release it.
- Release-time parking is atomic: `release_task` no longer decides on a separate
  pre-read. `park_task(..., only_if_unclaimed=True)` uses a conditional update
  (`status = 'open' AND claimed_by IS NULL`) and its row count as the decision, so
  a claim landing between the strike and the park is never swallowed.
- Board: parked and quarantined cards no longer offer the keyboard move affordance
  (`m`), so the move dialog cannot be opened on a card that can never be moved.
- Board: the Parked column is now actually fed — `useBoardData` fetches
  `status=parked` on load and applies the `task.parked` live event, so a card
  parked by the dispatcher moves out of its old column without a refetch.
- `reopen_task`: drop the dead `AND status != 'parked'` predicate (no row can fail
  it that has not already failed `status = 'closed'`), and cover the real
  invariant with a test that a parked card can be neither closed nor reopened.
