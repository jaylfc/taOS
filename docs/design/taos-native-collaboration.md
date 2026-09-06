# Moving work and comms into taOS

**Status:** PROPOSED. Needs Jay's decisions on the marked questions before slice 1.
**Date:** 2026-08-02
**Goal, in Jay's words:** put an idea into taOS, have an agent pick it up, ask
clarifying questions in Decisions, then spec, design and card it, without ever
opening Claude Code. Agents reply in realtime to messages and decisions inside
taOS. Claude Code stops being the primary place work happens.

## The honest answer: yes, except for one missing mechanism

Almost every part of this already exists. The blocker is not taOS, it is the
agent side, and it is a single specific gap.

**An LLM agent has no event loop.** It does not run continuously waiting for
messages. It acts only when its harness gives it a turn. Everything that looks
like realtime today, including my own A2A watcher, is a process running *inside
an already-open session*: when the session ends, the watching ends with it. That
is why the current setup depends on a Claude Code window being open. Nothing in
taOS can fix this, because the missing part is on the harness side.

**What changes the picture:** the Claude CLI supports headless invocation
(`claude -p`), a pinned `--session-id`, and `--resume`. So a small daemon can
hold the watch and INVOKE the harness when something arrives, creating the turn
that the agent needs in order to act. That is the whole trick, and it is
buildable now.

## What already exists

| Piece | State |
|---|---|
| Projects with nested typed elements | Built. `type` is free-form, so notes and lists are storable today |
| Project Files API | Built |
| Decisions app: create, answer, notify | Built, and agents can create decisions with a registry token |
| A2A bus and channels | Built, running on the Pi |
| Messages and chat | Built |
| Notifications | Built |
| Signal filtering (drop noise, keep real mentions) | Built, `a2a_filter.py` |

## What is missing

1. **A notes surface in Projects.** The storage exists; there is no organised
   notes-and-lists UI to put an idea into. Pure build, no blockers.
2. **Agent-state badges on entries** (see below). Small model plus UI.
3. **The wake daemon.** The real blocker.
4. **Cost governance.** Every wake spends tokens. Without gating this eats the
   weekly budget, which is already the binding constraint on how much the fleet
   can do.

## Badges: making agent state visible on the entry

Jay's ask, and it is the difference between "I threw an idea in a box" and "I
know what is happening to it". Every note, card, message and decision carries a
visible state:

- **Unseen** the agent has not read it yet
- **Seen** read, not yet acted on
- **Working** an agent is actively on it, with which agent shown
- **Question waiting** the agent asked something and is blocked on Jay, linked
  straight to the decision
- **Done** with what came of it, a card, a PR, a spec
- **Stalled** claimed but nothing has happened for N hours

Two properties matter more than the list itself. The state must be written by
the same mechanism that does the work, never by an agent remembering to update
it, or it will drift and become a lie. And **stalled must be computed, not
reported**: an agent that dies cannot mark itself stalled, and a dead agent
looks exactly like a working one otherwise. That is the same trap as a dead
watcher looking like a quiet one.

## Delivery: agent-pulls, harness-native, no external tooling required

**Corrected twice on 2026-08-02.** First draft spawned a headless session per
event, which is the expensive way. Second draft delivered by injecting into an
open pane with `herdr pane run`, which is cheap but **assumes the operator runs
herdr**. Jay's constraint, and it is correct: herdr is his local pane manager,
not something a taOS user has. A product feature cannot depend on it.

**The dependency has to be inverted. The agent connects to taOS; taOS never
reaches into the agent's terminal.**

That removes every assumption about how the agent is being run: a terminal, a
multiplexer, an IDE extension, the desktop app, or a headless box. taOS only
needs to expose events; how an agent notices them is the agent harness's problem.

### Cost discipline is a design constraint, not a later optimisation

Last week's usage was largely consumed by agents CHECKING the A2A bus. Realtime
and cheap pull in opposite directions, so the rule has to be built in from the
start rather than retrofitted.

**The measured facts, taken today rather than assumed:** only about 10 percent of
recent `build` traffic is automated noise, and the existing filter already drops
non-mentions and delivery acks. So the filter was never the problem.

**The problem was that "check the bus" was implemented as an LLM turn.** A timed
prompt that says "go and look" pays a full turn every time it fires, including
every time there is nothing there. Five agents checking hourly is 120 turns a
day of mostly empty checks, each reloading context before discovering there is
no work. That is how a week evaporates without producing anything.

**The rule, in one line: checking is mechanical, waking is gated.**

- **A poll costs nothing when a script does it.** A shell or Python watcher can
  hit the bus every few seconds forever at zero token cost, because no model is
  involved. Never spend a turn to discover emptiness.
- **A turn is spent only on a real signal**: addressed to this agent, not an
  auto-ack, not its own post, and not already handled. This is what
  `a2a_filter.py` does and it should be the shared reference implementation
  rather than something each agent reinvents.
- **Batch a burst.** A short settle window so five messages in a minute become
  one turn, not five.
- **Per-agent daily wake ceiling**, and when it is hit the agent stops waking and
  SAYS it has stopped. Going quiet at a limit is indistinguishable from being
  broken.
- **Recipient addressing removes the broadcast tax.** Until a message can be
  addressed to one agent, every agent must inspect every message to find out it
  was not for them. That is why the recipient field in the dependency chain is a
  cost feature as much as a routing feature.
- **Receipts make redelivery cheap.** Without them an agent cannot know it has
  already handled something, so the safe behaviour is to re-read, which costs
  again.

**Anti-pattern to name explicitly:** a cron whose prompt is "check X". If the
answer is usually "nothing", it must be a script that stays silent, not a prompt
that reports emptiness at full price.

### The transport is the A2A bus, and that is the point

Jay's intent, and it is the right call: **use the A2A bus (taOSmd) so any agent,
CLI or harness can join.** Everything above about being tooling-agnostic follows
from picking this transport rather than inventing a taOS-specific one.

Why it is already the right substrate:

- **Plain HTTP plus SSE.** Joining is an HTTP call, not an SDK. A watcher is
  roughly twenty lines in any language, which is what makes it harness-agnostic:
  the agnosticism comes from the protocol, not from a feature of Claude Code.
- **It is already multi-harness in production.** Agents from at least three
  different harnesses post to it today (claude-code, kilo and opencode lanes),
  which is proof rather than aspiration.
- **taOSmd is separable and embeddable**, so the bus is not chained to a taOS
  install.
- **It is deployed and carrying real traffic** on the Pi at :7900, capabilities
  `a2a.v1` alongside search, graph, temporal and tasks.

### The in-flight taOSmd work IS the dependency chain

This reframes several A2A cards that looked like scattered features. They are the
prerequisites for realtime collaboration, and should be sequenced as such:

| Need | taOSmd work | State |
|---|---|---|
| Address a message to ONE agent | recipient field | PR 228, in a fix round, has a blocking URL-injection issue |
| Know a message was received and acted on | read receipts | PR 224, rejected as unwired dead code, needs redoing |
| Jay as a first-class principal, not a self-claimed string | controller-signed human identity | PR 231, under review now |
| Who may read which channel | per-channel ACLs | PR 227, rejected, needs redoing |
| Unread counts and thread listing for badges | threads endpoints | PR 223, rejected, needs redoing |

Receipts and per-agent addressing are the two that the badge design depends on
directly: without receipts there is no honest "seen" state, and without
addressing every agent pays a turn for every message.

### The identity problem is the one to solve first

The bus is **unauthenticated on the LAN today**: `from` is a self-claimed string
and anyone can set it. That is tolerable for lane chatter. It is not tolerable
once Jay's ideas, decisions and answers flow across it, because a decision
answer that can be forged is worse than no decision system. PR 231 is exactly
this work, which is why it is the highest-value item in the chain.

### Honest limit that no transport removes

An SSE stream does not make an LLM agent an event loop. The agent still only
acts when its harness gives it a turn, so a held stream buffers frames the agent
reads on its next turn. The bus is the right transport regardless: it makes the
watcher small, portable and standard. But "realtime" here means seconds-to-turn,
not interrupt-driven, and the design should say so rather than imply otherwise.

### The layers

**1. Transport, universal: the taOS API.** Events over SSE where the client can
hold a stream, or a cheap poll where it cannot. This already exists and needs no
new mechanism.

**2. Noticing, harness-native.** Each agent runs a small watcher INSIDE its own
session using whatever its harness provides for background work. In Claude Code
that is a background process whose output surfaces as a notification on the next
turn: exactly what `a2a_watch.sh` already does for the A2A bus today, with no
external tooling involved. Another harness uses its own equivalent. The watcher
is session-local, so it lives and dies with the session, which is the honest
behaviour rather than a hidden dependency.

**3. Delivery when no session is listening.** This is the only case that needs
anything extra, and it is optional and pluggable:

| Adapter | Requires | Use |
|---|---|---|
| Session-local watcher | nothing beyond the harness | **default, ships to everyone** |
| Queue and deliver on next turn | nothing | universal floor, always works |
| Pane injection (herdr, tmux, ...) | that specific tool | operator convenience, ours |
| Headless resume | CLI headless support | unattended boxes |

**The floor must always work.** If no adapter is available, events queue in taOS
and the agent picks them up when it next takes a turn. Latency, not loss. Every
adapter above that is an optimisation, and none may be a prerequisite.

### The distinction that keeps this clean

- **Fleet ops tooling on our own box** (context watch, usage watch, the
  dispatcher) may use herdr freely. That is our infrastructure, not shipped.
- **taOS product features** must assume nothing beyond taOS itself and the
  agent's own harness. Realtime collaboration is a product feature.

Anything that blurs those two produces a feature that works only on Jay's
machine, which is the same class of mistake as hardcoding a LAN address.

### What this still needs

- **An agent registry with liveness**, so taOS knows which agents are connected
  and can show "unseen, agent offline" instead of silence. A dead agent must
  never look like a quiet one.
- **A durable per-agent queue with acknowledgement.** Delivery without a receipt
  is how a message vanishes into a session that has since died. The agent
  acknowledges, or the event stays pending and is redelivered.
- **Hard filtering**, reusing what `a2a_filter.py` already does, so only real
  signals cost a turn.

## Measured, not assumed: headless viability and cost

Tested on this Max subscription on 2026-08-02, no API key present, so these are
subscription numbers.

**Headless works.** `claude -p` returns clean JSON, `is_error: false`, and a
`session_id`. No API key required and no restriction encountered.

**Cost per wake, measured on a trivial prompt:**

| Mode | Cost | Cache creation | Cache read |
|---|---|---|---|
| Cold start (new session) | $0.164 | 7,347 | 15,251 |
| `--resume` an existing session | $0.031 | 388 | 22,598 |

The cold-start floor is the system prompt, CLAUDE.md and the memory index being
loaded *before any work happens*. **Resume is 5.3x cheaper per wake**, and that
ratio, not the daily count, is what decides whether this is affordable. At 50
wakes a day it is roughly $8 a day cold versus $1.55 resumed.

**Design consequence:** never spawn per event. Injecting into an open session is
cheaper than both rows above, and resume is the fallback for a session that has
been closed, not the primary path.

## Session identity and context lifecycle

Sessions are plain files: `~/.claude/projects/<path-slug>/<session-id>.jsonl`.
The session id is the filename, so tracking is a matter of recording one uuid
per agent and confirming the file still exists before resuming.

The catch with resume is that context grows monotonically. This session's
transcript is already 4.9MB. Left alone, a long-lived resumed session gets
slower, more expensive per wake, and eventually hits the context ceiling.

**We already solved this by hand, and the daemon should automate the existing
pattern rather than invent one.** `context_watch.sh` measures a session's token
usage and nudges at banded thresholds; `checkpoint_and_clear.sh` and the
`RESUME-*.md` handoff docs capture durable state so a fresh session can pick up
without re-deriving. The policy that falls out:

- **Preserve** by default: resume the pinned session, cheapest per wake.
- **Summarise** at a token band: write the handoff doc, start a fresh session,
  record the new id. The cost of one cold start is repaid within a few wakes.
- **Clear** deliberately when the work changes shape, so an agent is not
  carrying a finished project's context into a new one.

The durable memory is the handoff doc and taOS itself, never the transcript.
That is what makes clearing safe, and it is already how the agents work.

## Risk: this depends on a harness feature

Headless invocation working on a subscription is an external dependency we do
not control, and it has been discussed as something that might be restricted.
The mitigation is in the ordering: slices 1 and 2 deliver the notes surface and
the badges with timed pickup, which need none of this. Only the realtime upgrade
depends on it, and if that route closed, the fallback is a scheduled pickup on a
few-minute cadence, which is worse but not broken.

## Cost, stated plainly

Every wake is a paid turn. A chatty channel could wake an agent hundreds of
times a day and exhaust the weekly budget in hours. This is the single biggest
risk to the whole design and it needs deciding up front, not discovering later:

- Hard daily wake budget per agent, refusing to wake past it and telling Jay it
  has stopped rather than going quiet.
- Batching: a short settle window so five messages in a minute produce one wake.
- Tiering: free-model agents take the cheap work, expensive agents wake only for
  judgement.

## Phases

**Slice 1, notes and pickup.** A notes and lists surface in Projects. An agent
picks up new notes, asks clarifying questions as decisions, and turns the answer
into a spec and cards. Wake is still timed rather than realtime. This alone
delivers "I put an idea in and never open Claude Code", just with latency.

**Slice 2, badges.** Agent state on every entry, computed where it can be.

**Slice 3, the event queue and agent-side watcher.** Durable per-agent queue with
acknowledgement, liveness in taOS, and a harness-native watcher on the agent
side. Realtime replies with no external tooling required and no session
spawning.

**Slice 4, the rest of the fleet.** The sibling agents get daemons, and Claude
Code becomes a debugging tool rather than the venue.

Slices 1 and 2 are useful on their own even if 3 is delayed, which is the point
of the ordering: nothing here is all-or-nothing.

## Decisions needed before slice 1

1. **Where do notes live?** A first-class Notes element type inside a Project,
   or a dedicated Notes app that references projects? Cheaper inside Projects,
   more discoverable as its own app.
2. **Which agent owns idea intake?** Me, or the in-OS taOS agent that is already
   the user-facing one? This decides who Jay is talking to when he drops an idea.
3. **The wake budget number.** How many paid wakes per day is acceptable before
   the daemon stops and says so.

## What this does not change

Claude Code stays the place for debugging the fleet itself and for work that
needs a terminal. The goal is that Jay does not need it for the ordinary loop of
having an idea and getting it built.
