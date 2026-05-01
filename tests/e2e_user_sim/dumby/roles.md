# Existing Pi agents — role assignments for the Dumby the Dumbo project

The Pi already runs six agents, one per framework. Rather than deploy
fresh agents per role, the user-sim **reuses the existing six** and
addresses them by role in each chat brief. This exercises all six beta
frameworks with no setup overhead.

| Agent  | Framework         | Emoji | Color     | Role                              |
|--------|-------------------|-------|-----------|-----------------------------------|
| john   | openclaw          | 👻    | `#ef4444` | Writer                            |
| tom    | hermes            | 🤖    | `#3b82f6` | Editor                            |
| don    | smolagents        | 🐝    | `#10b981` | Illustrator                       |
| linus  | langroid          | 🧠    | `#a855f7` | Web Designer                      |
| pat    | pocketflow        | 🌊    | `#f59e0b` | Marketing Strategist              |
| olive  | openai-agents-sdk | 🫒    | `#06b6d4` | Producer / cross-review           |

These agents already exist on the Pi (`/api/agents` lists them). The
user-sim **does not** create or delete them — only attaches them to the
Dumby project as Members and addresses them in the project chat.

## Role context (passed in each chat brief, not as a persona edit)

The existing personas on these agents are generic ("You are John, a
curious agent…"). The user-sim does not modify them. Instead, every
brief in the project chat starts with a one-line role hat:

> @john — *acting as Writer for Dumby the Dumbo* — please draft Chapter 1.
> Target: ~400 words, ages 4–8, gentle warmth, short paragraphs, concrete
> imagery. Hand off to @tom (Editor) when done.

Treat the agent as smart enough to take the role from the brief. If they
don't, that's a finding worth recording.

## Role briefs (use these verbatim or close to it)

### Writer brief (john)
> Acting as Writer. You are drafting "Dumby the Dumbo" — a kids' book
> about a clumsy young elephant who learns being himself is enough. The
> story is for ages 4–8. Write short paragraphs, concrete imagery,
> believable child dialogue, gentle warmth. Deliver chapter by chapter,
> never the whole book at once. Hand each draft to @tom for review.

### Editor brief (tom)
> Acting as Editor. Critique drafts from @john for a children's picture
> book audience: pacing, age-appropriateness, character voice,
> read-aloud quality. Numbered, actionable notes — not vague praise.
> Approve or send back to @john for revision.

### Illustrator brief (don)
> Acting as Illustrator. Read each approved chapter from @john. For each
> scene you decide subject, mood, palette, composition. Use the Images
> app (Open Images on the dock) to render covers and scenes. Save them
> into the Dumby project Files area. Explain your choices in the project
> chat so @john and @tom can react.

### Web Designer brief (linus)
> Acting as Web Designer. Build a single-file `index.html` landing page
> for "Dumby the Dumbo": mobile-first, system font stack, no external
> JS, accessible (semantic landmarks, alt text on images). Embed the
> cover image @don generated. Save the file into project Files. Review
> @pat's marketing copy and call out any web-readability issues.

### Marketing Strategist brief (pat)
> Acting as Marketing Strategist. Read the latest drafts from @john, the
> editorial notes from @tom, the cover by @don, and @linus's landing
> page. Write `strategy.md` (positioning, audience, channels) and
> `social.md` (a 4-week plan suitable for a first-time self-published
> author). Save both into project Files.

### Producer brief (olive)
> Acting as Producer / cross-review coordinator. After everyone has
> shipped their primary artifact, you read the whole package and post a
> final go/no-go note in the project chat. You also nudge anyone whose
> hand-off is stuck.

## Handoff protocol

Every handoff message must:
1. Address the next agent by `@name`.
2. State the handoff type: `DRAFT`, `REVIEW`, `CRITIQUE`, `REVISION`,
   `ARTIFACT`.
3. Reference the previous artifact (file path, image filename, or
   message timestamp).

## Cross-review pass (after primary artifacts exist)

- @john critiques @linus's `index.html` from a story-fit perspective.
- @don critiques @pat's `social.md` from a visual/brand perspective.
- @olive reads everything, posts the final go/no-go note.

Critiques get posted to the project chat; the relevant agent revises if
their owner accepts the note.
