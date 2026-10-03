### Added

- Activity app: a **Model Activity** feed, a live timeline of model-level
  events (load, unload, eviction, shrink, route change, and inference request
  start/finish with duration and token rate) with filters for worker, model and
  event type. Backed by a bounded ring buffer on the controller, streamed to the
  app over SSE (`GET /api/activity/models/stream`, history at
  `GET /api/activity/models`). Request, token-rate and failover events come from
  the LLM gateway; the model load/unload/eviction/shrink hooks are on
  `CoreAwareModelScheduler` and start reporting once that scheduler is
  constructed with the feed. This is a separate surface from the AI-stack
  manager already in the Activity app's header.

### Security

- `/api/activity/models` and `/api/activity/models/stream` are session-only and
  owner-scoped. Both require a session cookie: the host local-token bearer,
  which the middleware otherwise accepts, is answered with `401`. A gateway
  event carries the principal that made the request (`user:<id>`, an agent's
  registry name, or the gateway master-key label) on `request.start` /
  `request.finish` and on `model.route`. An admin session sees the whole ring,
  and any other session sees only the events its own principal owns, so one
  user cannot read which models another user's agents call, how often, or under
  which agent names. Controller-level events (scheduler load / unload / evict /
  shrink), which have no owner, stay admin-only.
- The SSE route subscribes and unsubscribes inside its generator, so a client
  that disconnects before the response body is iterated no longer leaves a
  subscriber queue behind for producers to keep fanning events into.
