# Live Surface Law

**Status:** Active, design law. Every implementer and reviewer applies this to
every taOS surface: windows, panes, embedded views, and PWAs.

The default state of every taOS surface is **live**. A surface that only fetches
on mount and then goes stale until the user manually refreshes is a defect, not
a style choice.

## 1. Auto-refresh on focus and visibility

Every surface MUST refetch its data when it regains focus or becomes visible
again. The **S1 hook** is the intended mechanism once landed (see PR 2220): a
hook that listens for `focus` and `visibilitychange` events and triggers a
refetch. Fetch-on-mount-once is forbidden.

## 2. Push updates via the OS event stream

Every surface MUST subscribe to the OS event stream for push updates. The
**S2 hook** is the intended mechanism once landed (see PR 2220): a hook that
opens the SSE connection and dispatches events to the surface's state layer.
Polling is a fallback only when the stream is unavailable, never the primary
path.

## 3. Motion that aids comprehension

State changes animate where motion aids comprehension: list insert/remove, card
move, status flip, pending-to-ready transitions. Transitions are smooth and
respect `prefers-reduced-motion`. Low-end and Pi hardware paths receive reduced
or no motion. Motion where needed, not everywhere.

## 4. Fix-while-there

Any PR that touches an existing app or surface that does not follow this
convention MUST bring that surface up to the convention in the same PR: adopt
the S1/S2 hooks (once landed, see PR 2220), add the missing transitions. The
only exception is when doing so dwarfs the original change; in that case the PR
description must name the gap and a card must exist for it.
