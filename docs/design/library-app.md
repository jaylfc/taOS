# Library app: universal ingestion into memory and collections

Status: owner-approved direction (2026-07-20), design locked for slice 1 to 3;
section 6 is a pre-registered research spike, not a commitment.

## 1. What it is

The Library is the dumping ground. Any info, file, or media in any format gets
dropped in (drag-drop, share sheet, URL paste, agent send), and the Library
processes and/or ingests it into memory and collections. Agents and apps are
then granted different access levels to the relevant collections. Nothing else
in taOS accepts arbitrary input; everything funnels here.

Relationship to existing pieces:

- **taosmd collections** (approved 2026-07-19): the Library is the taOS-side
  PRODUCER for collections. It writes files under allowed roots and calls the
  collections API (create, index, link, grant). It adds no retrieval semantics
  of its own.
- **Files app**: general filesystem browsing stays in Files. The Library is
  intake + processing + provenance, not a file manager.
- **Images Studio / future .aai work**: same philosophy (derivative plus
  metadata that agents consume cheaply); the Library is where that pipeline
  will hang off later.

## 2. Architecture

```
   drop/paste/send            LibraryStore (SQLite, BaseStore)
        |                        items(id, kind, source_url, title, status,
        v                        storage_path, bytes, created_at, ...)
  POST /api/library/ingest  ->   artifacts(item_id, kind, path, meta_json)
        |                        jobs(item_id, stage, state, error)
        v
  Ingest pipeline (async, per-kind processors, job rows drive retries)
        |
        +--> cheap tier: metadata, thumbnail, description, transcript, OCR text
        +--> heavy tier (opt-in): full media download, quality choice
        |
        v
  Collections handoff: write text artifacts into a per-target folder under an
  allowed root, then taosmd collections index; link to project; grants stay
  EXPLICIT (no transitivity - same rule as the collections verdict).
```

- One `LibraryStore` following the SCHEMA/MIGRATIONS discipline.
- Processors are registered per detected kind (url:youtube, url:web, pdf,
  image, audio, video, text, archive). Detection at ingest, stored on the item.
- Every artifact records provenance: source URL, fetch time, processor
  version. Reprocessing is idempotent per (item, stage).
- Access levels: the Library UI is admin-session; agent access to CONTENT goes
  only through collection grants. The Library never exposes raw storage paths
  to agents.

## 3. Ingestion tiers

**Cheap tier (always, automatic):** everything that costs kilobytes.
For a YouTube URL: canonical link, title, channel, description, thumbnail,
duration, upload date, subtitles/transcript (all available languages),
chapters. Stored as artifacts; the TEXT artifacts (description, transcript,
chapters, any OCR) are what get indexed into the collection - this is the part
agents actually query.

**Heavy tier (opt-in, per item or per source rule):** the media itself.
Download with a preferred-quality setting (per-user default plus per-item
override), storage accounting shown in the UI, and a per-source cap. Runs
through yt-dlp for URL sources. Personal archiving on the user's own instance;
the user carries responsibility for what they download, and the Library never
redistributes.

## 4. YouTube reference flow (slice acceptance)

1. Paste a YouTube URL into the Library (or share it from the Browser).
2. Within seconds the item card shows thumbnail, title, duration, channel.
3. Cheap artifacts land: description, transcript, chapters.
4. The item is linked to a chosen collection; indexing runs; an agent granted
   that collection can answer "what does the video say about X" from the
   transcript without any video download having happened.
5. Optionally the user (or a source rule) triggers the heavy download at the
   preferred quality; progress and final size are visible on the card.

## 5. Access levels

- Collection grants are the ONLY agent-facing access surface (explicit per
  collection, offered-but-never-implied by project links, per the locked
  collections verdict).
- Apps get read access through the same grant mechanism using their app
  principal (ties into the app permission/capability system when it lands;
  until then, apps go through the owning user's session like today).

## 6. Video storage strategy (pre-registered research spike, NOT slice 1-3)

The question: can we store a LOW quality download and upscale at playback,
without losing information that matters?

Three sub-hypotheses, each with an honest measurement before anything ships:

1. **Low + upscale playback.** Store 480p-class video, upscale on playback.
   Viable only where playback hardware allows (RTX 3060 lane; the Pi tier gets
   upscale-once-and-cache or plain low-quality playback). Measure: disk saved
   vs a perceptual metric (VMAF) against the source, on a fixed 10-video set
   chosen before the code exists.
2. **HQ keyframes as upscaling references.** Save full-quality keyframes at
   scene changes; use reference-based super-resolution so the upscaler
   reconstructs detail from the exemplars. This is a real technique, not
   speculation, but the win must be measured: VMAF delta with vs without
   references on the same set.
3. **Text-region lossless screencaps.** At ingest, sample frames, run text
   detection/OCR, score text density, and save the top text-heavy segments as
   lossless stills PLUS their OCR text. Two payoffs: the stills guarantee
   nothing legible is lost to compression, and the OCR text goes straight into
   the collection where it is worth more to agents than the pixels. The OCR
   path ships with the spike regardless of how 1 and 2 measure, because it is
   cheap and its value does not depend on the upscaling outcome.

Kill criteria (pre-registered): hypothesis 1 or 2 ships as a default only if
disk saving is at least 60 percent AND mean VMAF stays above 85 on the eval
set AND playback startup overhead is under 2 seconds on the target tier.
Otherwise the feature stays a flagged experiment and the numbers get published
in the research notes. Hypothesis 3 ships on its own merits (OCR accuracy
spot-check only).

## 7. Phasing

- **P1 Library core:** LibraryStore + ingest endpoint + pipeline skeleton +
  file/text/pdf/image processors (cheap tier only) + collections handoff +
  Library app UI (drop zone, item cards, status). Depends on taosmd
  collections Phase 1.
- **P2 URL ingestors:** YouTube cheap tier end to end (section 4 steps 1-4) +
  generic web-page ingestor (readability extract + screenshot).
- **P3 Heavy tier:** opt-in download, quality preference, storage accounting,
  per-source rules (section 4 step 5).
- **P4 Spike:** section 6, on the Fedora 3060 lane, results published before
  any default changes.

Lanes: P1/P2/P3 backend is the hognek lane; Library app UI and item-card
frontend are fleet cards; P4 is a research spike owned by the lead with fleet
help on the eval harness.

## 8. Non-goals (v1)

- No editing of media (Images Studio / future studios own that).
- No public sharing of library items (taOSnet later).
- No automatic deletion; storage pressure surfaces in the UI, the user decides.
- No new retrieval API; collections are the only query path.
