# SLO-aware scheduler (spec addendum, GH #151)

## Motivation

Today's scheduler is "cheapest eligible worker wins". That's a fine
default when every request is interactive and every worker is local,
but it falls over the moment a user wants:

- "Messages from my personal agent should feel instant (<500ms TTFT),
  background batch jobs can take whatever"
- "Document classification over 10K files must finish by 9am, I don't
  care which worker runs it"
- "Voice assistant has a hard 1s latency budget; drop batch jobs to
  keep it under"

These are three different SLO classes — latency, throughput, and
deadline — and the scheduler currently treats them all the same.

SLO-aware scheduling means requests carry a class, the scheduler
picks workers based on the class (fastest for latency, most free for
throughput, cheapest for batch), and the scheduler can preempt one
class for another when a higher-priority request arrives.

## SLO classes

| Class | Definition | Scheduling bias |
|---|---|---|
| `interactive` | TTFT < 500ms, decode steady state | Prefer lowest-loaded worker with resident model |
| `latency` | TTFT < 100ms hard cap, like voice or gaming | Prefer workers pinned to the model, evict other work if needed |
| `throughput` | Maximise tokens/sec, no TTFT guarantee | Prefer highest-VRAM worker, batch where possible |
| `batch` | Deadline-based, seconds to minutes is fine | Run on cheapest worker, preempt for anything higher |
| `bulk` | Hours to days, job queue semantics | Run only on idle cycles |

Classes are a fixed enum. Users can tag agents with a default class,
and per-request overrides are allowed.

## Wire-level surface

Requests carry an optional `slo_class` field:

```json
POST /api/agents/<id>/chat
{
  "messages": [...],
  "slo_class": "interactive"
}
```

Agents carry a default class in config:

```yaml
agents:
  - name: voice-assistant
    slo_class: latency
  - name: batch-labeler
    slo_class: batch
```

If neither is set, the default is `interactive`.

## Scheduler changes

The existing `task_router.py` picks a worker by sorting eligible
workers by load. SLO-aware routing adds two layers on top:

1. **Class filter.** Some workers are marked as "reserved for latency
   class" — others can't use them. The filter removes ineligible
   workers before the load sort.
2. **Preemption check.** If an `interactive` request arrives and the
   best worker is running a `batch` job, the scheduler can signal the
   backend to pause the batch and serve the interactive request first.

Preemption needs backend cooperation. llama-cpp + continuous batching
can drop a batch stream mid-decode; vLLM has native preemption. ollama
has no preemption path — those workers just don't accept preemptible
classes.

## Preemption semantics

When a higher-class request preempts a lower-class one:

- Lower-class request's current token keeps streaming
- No new tokens are generated for it until the higher-class request
  completes
- The scheduler remembers the preempted state and resumes after

The preempted request sees this as a stall, not a failure. If the
stall exceeds a timeout, the preempted request can be migrated to
another worker (effectively a retry).

## Deadline class (batch + bulk)

For `batch` jobs with a hard deadline, the scheduler also tracks a
deadline field and runs earliest-deadline-first within the class.
`batch` jobs that would miss their deadline get promoted to
`interactive` in the last hour of their budget, automatically.

`bulk` class is never promoted. If a bulk job can't finish on idle
cycles, it fails its deadline and reports.

## Reservations

Users can reserve a worker for a class:

```yaml
workers:
  - name: gpu-box
    reserved_for: latency    # only latency class runs here
```

This is the escape hatch for users who don't want the voice assistant
to be anywhere near the batch job.

## Telemetry

The scheduler exposes per-class metrics on `/api/scheduler/metrics`:

- Requests per class per second
- p50 / p95 / p99 TTFT per class
- Deadline-miss count per class
- Preemption count per worker

These feed the Cluster page dashboard and the activity log.

## Open questions

- Should the scheduler try to *predict* whether a request will meet
  its SLO, and refuse it upfront if it can't? Nice in theory, very
  hard in practice. v1 does not do this.
- Should `latency` class workers auto-pre-load models they are
  reserved for? Probably yes — the whole point of reservation is to
  avoid cold starts.
- How do we expose this without overwhelming novice users? The
  Agents wizard probably shouldn't show SLO classes at all; they
  default to `interactive`, power users edit the YAML.

## Dependencies

- Backend preemption hooks (llama-cpp, vLLM)
- Scheduler refactor to separate class-filter from load-sort (small)
- Metrics plumbing for per-class counters (small)
- User-facing reservation UI (medium, can be deferred to v0.4)

## Incremental path

v0.3 can ship **class tagging + class-aware sort** without preemption
or reservations. That alone delivers most of the user value: a voice
agent goes to a different worker from a batch job by default. Preempt
and reserve are v0.4.

## Tracking

GH #151. Shares the metrics plumbing with #218 (detailed /api/health).
