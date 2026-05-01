# Dumby the Dumbo team — coordinator-driven model

The user-sim **does not script the creative workflow turn-by-turn**. Jay
gives the kick-off brief to a single coordinator (Tom). Tom is then
responsible for allocating work, checking it, iterating, and seeing the
project through to delivery. Every other agent receives instructions
*from Tom*, not from Jay.

This is a real autonomy test for the multi-agent stack: can Tom
actually drive the team end-to-end via the project a2a channel?

## The team Tom is briefed about

| Agent  | Framework         | Emoji | Color     | Strength to lean on                              |
|--------|-------------------|-------|-----------|--------------------------------------------------|
| **tom**    | hermes            | 🤖    | `#3b82f6` | Coordinator + Senior Editor (this is *you*)      |
| john   | openclaw          | 👻    | `#ef4444` | Strong narrative/prose drafting                  |
| don    | smolagents        | 🐝    | `#10b981` | Image generation + visual art (Images app)       |
| linus  | langroid          | 🧠    | `#a855f7` | Multi-step reasoning + HTML/web                  |
| pat    | pocketflow        | 🌊    | `#f59e0b` | Graph/flow thinking — marketing strategy + plans |
| olive  | openai-agents-sdk | 🫒    | `#06b6d4` | Final QA cross-review                            |

All agents run on `kilo-auto/free`, all are members of the Dumby
project, and Tom has `can_edit_canvas` so he can post storyboard
sketches / scene notes to the project canvas.

## Tom's coordinator brief

Jay pastes this verbatim into the project messages tab as the very
first message of the session.

> @tom — *acting as Project Coordinator and Senior Editor for "Dumby
> the Dumbo"*. You're driving this project end-to-end. Jay (the human)
> will not allocate tasks or chase agents — that's your job. You will
> see the project through to delivery.
>
> **Important — strict @-mention rule.** This project channel only
> routes messages to agents who are @-mentioned by name (e.g. `@john`,
> `@don`). A reply that doesn't @-tag anyone reaches no one. When you
> brief teammates **explicitly tell them**: "always @-tag the next
> agent in your reply when handing off; without an @-tag the message
> won't be routed and the project stalls." You are designated as
> **lead** — the channel is configured so you receive every message
> regardless of mentions, so you can chase silent teammates. If a
> teammate goes quiet for two turns after their reply, @-mention them
> again with a polite nudge.
>
> **Kanban awareness.** The project has a Tasks tab (kanban board).
> To create a task, write a `/new` verb line directly in your message:
> `/new "<title>" @<name>`
> e.g. `/new "Draft Chapter 1" @john`
> The board picks this up automatically — no need for `[TASK]` markers.
> When work is delivered, close the task with:
> `/close <tsk-id> <optional outcome note>`
> e.g. `/close tsk_3a9f21 chapter 1 draft complete`
> Task IDs are shown on the kanban card. You don't have to use these —
> the test will run regardless — but it makes progress visible on Jay's
> mobile board and keeps the team aligned on what's done.
>
> **Your team (all already members of this project, all on
> `kilo-auto/free`):**
> - @john — openclaw — strongest at narrative prose. Use for chapter
>   drafting and revisions.
> - @don — smolagents — image generation. Has access to the Images app
>   for cover art and scene illustrations. Save outputs to project Files.
> - @linus — langroid — strong on structured/web work. Use for the
>   landing-page `index.html`.
> - @pat — pocketflow — flow/strategy thinker. Use for marketing
>   strategy + social plan.
> - @olive — openai-agents-sdk — your QA reviewer. Loop her in for
>   final cross-review of the whole package.
>
> **The brief:** "Dumby the Dumbo" is a kids' picture book for ages
> 4–8. Synopsis: a clumsy, ditsy young elephant called Dumby learns
> that being yourself is enough by accidentally saving a flock of
> flamingos one giggle at a time. Target: ~3 short chapters (~400
> words each), gentle warm tone, concrete imagery, believable child
> dialogue.
>
> **Deliverables you're responsible for:**
> 1. 3 chapters drafted, edited, revised
> 2. Cover illustration + ≥2 scene illustrations (don, via Images app)
> 3. A single-file mobile-first `index.html` landing page (linus,
>    saved to project Files)
> 4. `strategy.md` + `social.md` marketing pack (pat, saved to Files)
> 5. Final go/no-go review note from @olive on the whole package
>
> **Your tools:**
> - The project messages channel — @-mention teammates, brief them,
>   collect their work, give feedback
> - The project canvas — you have edit access. Post storyboard ideas,
>   scene notes, sketch prompts here so the team can riff on them
> - The project Files area — final artifacts go here
>
> Start by laying out your plan: who you're going to brief first, in
> what order, and what hand-offs you expect. Then begin executing.
> Tell me (Jay) when each phase is done. If you get stuck, say so
> explicitly and what you need.

## The user-sim's actual role this session

1. Post Tom's coordinator brief above in the project messages tab.
2. Watch Tom reply with a plan.
3. Watch Tom @-mention teammates. The a2a channel routes mentions
   correctly now (post-#286), so each agent will reply when called.
4. Don't intervene unless Tom is stuck for >5 minutes with no output.
   If Tom reports a blocker, surface it (don't try to be the
   coordinator).
5. Optionally create kanban tasks reflecting Tom's plan so the board
   shows live progress on Jay's phone.

## What success looks like

- Tom posts a coherent allocation plan
- Tom @-mentions teammates and they reply
- Artifacts (chapter drafts, images, HTML, marketing docs) accumulate
- Tom posts ≥1 storyboard / scene note to the canvas
- Olive eventually posts a final go/no-go note
- Tom signs off

## What "interesting failure" looks like (also valuable)

- Tom doesn't allocate — just chats
- Tom @-mentions but ignores the replies
- Cross-agent handoffs break down — record exactly where
- Tom claims completion before all deliverables exist
- Any agent silently no-ops when @-mentioned — record this; it's a
  real product finding
