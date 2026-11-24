/**
 * The taOS account's device roster (docs/proposals/account-device-sync.md,
 * build-order item 3). The roster service on taos.my does not exist yet, so
 * registering is a no-op that reports "pending": setup carries on, and the
 * device is registered once the roster ships. Replace this body then, and
 * keep the signature.
 */
export type RosterResult = { status: "registered" } | { status: "pending" } | { status: "error"; message: string };

export async function registerThisDevice(): Promise<RosterResult> {
  return { status: "pending" };
}
