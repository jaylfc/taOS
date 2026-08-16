#!/usr/bin/env python3
"""Derive the resume-pair PRIMARY arm time from the usage watcher's ACTUAL cron spec.

Usage:  resume_arm_time.py <resets_at ISO8601> [margin_seconds]
Prints: the arm instant (UTC), the 5-field cron line for a one-shot, and the
        evidence it used, so the caller can check the derivation rather than
        trust the number.

WHY THIS EXISTS (2026-08-16, bus 2774/2776/2777/2779, @taOSc-dev).
The resume pair kept waking inside the window where /home/jay/.taos-usage/current.json
still reports the PREVIOUS window's utilization. The gate read a file written by a
different component on its own schedule, so the wake landed in the gap by construction.

We fixed it three times and the first two fixes were the same mistake at different
scales:
  +2min  - tuned to nothing, always wrong.
  +7min  - tuned to the two instances in hand; still fails for r in {6,7,8} and is
           marginal at r=9, which is where BOTH real windows sit.
  +11min - correct for TODAY, but it is only `cadence + 1`. Nothing at the three
           hardcoded sites pointed back at the crontab line that made it true, so
           widening that cron to */15 would silently re-create the bug with no
           constant appearing to change.

So this script does not encode an offset at all. It reads the watcher's real cron
spec and arms strictly after the FIRST TICK FOLLOWING THE RESET, which is correct
for any phase AND any cadence, including a NON-UNIFORM tick list. Note the minute
field is an enumeration (`6,16,26,36,46,56`), not `*/10`: its uniformity is
incidental and must not be assumed.

It FAILS LOUD if it cannot find the watcher line. A gate whose PASS is reachable
from zero data is not a gate: silently falling back to a constant is exactly the
failure this replaces.

The offset is a LATENCY property, not the correctness fix. The correctness fix is
to gate on the STDOUT of usage_publish.sh and never on a re-read of current.json,
because that script does not write current.json at all.

MARGIN, and it is measured rather than assumed (2026-08-16). The margin only has to
cover the watcher's own run duration, i.e. the gap between the tick firing and
current.json being fully written. Two samples of that gap, taken from the file's
mtime against its tick: 2.336s (tick :56:00) and 1.755s (tick :36:00). So the write
lands about 2 seconds after its tick and the 60s default is roughly 25x headroom.
This also retires an open worry: the earlier +7 arm was NOT in fact racing the write
at r=9 (it woke 59s after the tick, which is ~28x the observed write time). +7's real
and sufficient defect was that it fails OUTRIGHT for r in {6,7,8}, which is a phase
error, not a race. Do not carry "+7 was marginal" forward as a race claim; it was
wrong for a different reason.
"""
import datetime
import getpass
import re
import subprocess
import sys
import time

# The two tunables, each stating WHAT IT IS A FUNCTION OF. That is the whole point of
# this file: a constant whose dependency is unnamed is the bug it exists to prevent.
#
# MARGIN_SECONDS is a function of the WATCHER'S WRITE DURATION, and it is MEASURED:
# current.json's mtime landed 2.336s and 1.755s after its tick in two samples
# (2026-08-16). 60s is ~25x that. Re-measure if watch.sh grows work.
MARGIN_SECONDS = 60
#
# RETRY_LEAD_SECONDS is a function of THE PRIMARY'S FIRE-TO-DELETE LATENCY: how long the
# primary wake needs, from the instant cron fires it, to delete the retry. The retry must
# not fire before that deletion. MEASURED (2026-08-14..16, from JSONL session transcripts,
# 2646 scanned): primary cron fire -> CronDelete of the retry.
#   2026-08-14T11:02:00.636Z -> 11:02:08.861Z  =   8.225s  (retry 006c193f, 11:00Z 5h reset, 9a70afb2)
#   2026-08-14T16:02:00.600Z -> 16:02:50.329Z  =  49.729s  (retry 8fc267d6, 16:00Z 5h reset, 6c6b87ec)
#   2026-08-14T21:02:00.422Z -> 21:02:03.874Z  =   3.452s  (retry 537c3e51, 21:00Z 5h reset, 6c6b87ec)
#   2026-08-15T02:02:00.790Z -> 02:02:24.992Z  =  24.202s  (retry 1b93ed60, 02:00Z 7d reset, f03f3af1)
#   2026-08-16T02:02:00.849Z -> 02:02:04.960Z  =   4.111s  (retry 5ff29d1b, 01:59:59Z 7d reset, f03f3af1)
#   2026-08-16T03:02:00.293Z -> 03:02:04.895Z  =   4.602s  (retry 8ea092dd, 03:00Z 5h reset, 179fcc0b)
#   2026-08-16T08:11:00.322Z -> 08:11:16.318Z  =  15.996s  (retry 649adf47, 07:59:59Z 5h reset, 85a5f4a)
# Max observed: 49.729s (the 16:02Z wake, 6c6b87ec). 60s is above the max.
# Re-measure when the primary's wake path changes: each sample is a primary-wake ->
# first-CronDelete pair from a resume JSONL transcript.
#
# THE TABLE ABOVE IS EVIDENCE I HAVE NOT INDEPENDENTLY RE-DERIVED. It arrived by an
# unattributed write into this shared directory and is kept as documentation, NOT as the
# justification for a value change. A previous version of this block cited "the RED witness in
# test_resume_arm_time.py"; that witness does not exist in that file, and the suite stayed
# GREEN over the dangling citation (@taOSmd-dev, bus 2840). Citation removed rather than left
# pointing at nothing.
#
# THE LEAD IS NOT THE GAP, and both @taOSmd-dev and I argued about the wrong quantity first.
# retry_after() does not place the retry at primary+lead; it walks to the next WATCHER TICK
# whose fire clears the lead, so the tick lattice quantises the result. Reproduced here at the
# production spec (*/10 offset 6, reset 13:00Z, primary fire 13:07Z):
#     lead 0 / 60 / 599 / 600  -> retry 13:17Z, armed gap  600s   (identical)
#     lead 601                 -> retry 13:27Z, armed gap 1200s
# One boundary in the whole range. The suite already said so and we both read past it:
# "lead=-900: retry STILL lands after the primary" is lattice dominance, stated as a PASS.
#
# 60s is the smallest lead the production lattice can satisfy (the next tick is always >= 60s
# away) and is above the measured max of 49.729s. Under a dense spec where gap can equal lead,
# 60 leaves headroom against the observed max. Re-measure if the primary's wake path changes.
RETRY_LEAD_SECONDS = 60
#
# MIN_LEAD_SECONDS is a function of UPSTREAM ROLLOVER PROPAGATION: how long after the
# nominal reset instant the usage API actually serves the new window. That quantity is
# NOT MEASURED and 30 is a guess, stated as one rather than dressed as a derivation
# (@taOSc-dev, bus 2782, correctly noting it names nothing). It only binds when a tick
# falls within 30s of a reset, which the current 6-mod-10 spec never does; it was added
# for wide/aligned specs like */15 where a tick can land 0.2s after the reset. TO
# MEASURE: at a reset boundary, poll the API each second and record when the returned
# window flips. Until then this is the one honestly-unfounded number in the file.
MIN_LEAD_SECONDS = 30
#
# SCHEDULER_EARLY_JITTER_SECONDS is a function of THE SCHEDULER'S OWN CONTRACT, which is a
# layer MARGIN_SECONDS never named and which can undo it entirely. CronCreate documents:
# "one-shot tasks landing on :00 or :30 fire up to 90 s early". The resume pair are
# one-shots. So an arm computed at :00 or :30 with a 60s margin can fire 90s early, i.e.
# 30s BEFORE its own tick - exactly the race the margin exists to prevent, reintroduced by
# a dependency the margin did not declare.
# MEASURED by enumeration, not assumed: with ticks at :29 or :59 and the default margin the
# derived arm lands on :30 / :00 with a 60s lead, and 4 of the 6 reachable
# (tick, margin) combinations have a lead under the 90s jitter. TODAY'S REAL SPEC IS SAFE
# (ticks 6..56 arm at :07..:57, none jittered), which is precisely why this went unnoticed:
# correct by instance again. Found by reading the scheduler contract after @taOSc-dev
# (bus 2795) argued the conversion might not need to exist at all.
SCHEDULER_EARLY_JITTER_SECONDS = 90
JITTERED_MINUTES = (0, 30)
#
# A FOURTH LAYER, NAMED BUT NOT MODELLED (@taOSc-dev, bus 2798 (2); recorded here at the
# 08:4xZ retrospective, which caught that I had promised to record it HERE and had instead
# recorded it only on the bus and in my handoff - the two places nobody editing this file
# reads). DELIVERY: the scheduler states "Jobs only fire while the REPL is idle (not
# mid-query)." An arm derived correctly, encoded correctly, and jittered inside its declared
# 90s is still not delivered at its denoted instant if the REPL is busy then.
#
# Two asymmetries make this worse than the jitter, and neither is handled below:
#   - MARGIN_SECONDS is a ONE-SIDED guard. It buys clearance so the wake lands AFTER the
#     watcher's write, i.e. it bounds EARLY. Idle-gating is purely LATE, and unlike the 90s
#     the contract states NO BOUND on it at all.
#   - The inversion refusal in main() is a DERIVATION-time check on two cron lines. It can
#     only assert that the DENOTED order is primary-then-retry. IT CANNOT ASSERT THE FIRE
#     ORDER. What happens when two one-shots come due during one non-idle stretch and are
#     released together is NOT MEASURED and is not asserted here either way.
#
# Deliberately NOT fixed: every previous round of this file was made worse by acting on a
# mechanism nobody had read. TO MEASURE: arm two one-shots a minute apart, hold the REPL
# busy across both, and record the delivered order and lag.

# All FIVE cron fields are captured, not just the minute. The first version consumed
# hour/dom/month/dow as unanchored \S+ and never looked at them, so an hour-restricted or
# weekday-only watcher line matched, nothing failed, and the script derived an arm time
# asserting a write that never happens (@taOSc-dev, bus 2785; reproduced: a `... 8 * * *`
# watcher yielded a derived tick at 15:06 while the watcher only runs at hour 8). That is
# the ORIGINAL staleness bug rebuilt, with `EVIDENCE (read, not assumed)` printed above it.
# We do not model those fields, so we REFUSE them rather than model them wrong.
WATCHER_RE = re.compile(
    r"^([0-9,\-*/]+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+.*taos-usage/watch\.sh")


def parse_minutes(spec, line):
    """The minute field -> a sorted tick list, REFUSING anything it does not model.

    (@taOSc-dev, bus 2790 (A); every case below reproduced before fixing.) WATCHER_RE
    admits `[0-9,\\-*/]+`, which is a strictly larger language than this parser models.
    Previously anything in the gap either reduced to an empty set and fell out of the
    caller's loop into "the crontab WAS read, but it contains no watch.sh line" - FALSE,
    the line is right there and it matched - or died on a bare ValueError with no
    diagnosis at all:
        `5-2`   -> range(5,3) -> empty -> the false watcher-gone message
        `*/0`   -> ValueError: range() arg 3 must not be zero
        `-` `,,` `1--2` -> ValueError from int()/unpack
    That is exactly the collapse this module refuses 60 lines earlier for the crontab
    read: telling the operator the staleness model is GONE when the model is intact and
    it is the PARSE that failed sends them to re-derive something that is fine. The
    split is only worth having if every failure lands in the right half, so an
    unmodelled spec now gets its own refusal naming the spec, and the caller's
    fall-through is reachable ONLY when no line matched.
    """
    def bad(why):
        return SystemExit(
            f"FAIL: the watcher line MATCHED but its minute field {spec!r} is not one\n"
            f"this script models ({why}).\n"
            f"  line: {line}\n"
            "This is NOT 'the watcher is gone' and NOT 'the crontab is unreadable'. The\n"
            "schedule is present and the PARSE failed, so the staleness model is intact\n"
            "and does not need re-deriving. Modelled forms: `*`, `M`, `A-B` (A<=B),\n"
            "`*/S` (S>=1), and comma-separated combinations of those.\n"
            "REFUSING to guess a tick list rather than derive an arm time from one."
        )
    mins = set()
    for part in spec.split(","):
        if part == "":
            raise bad(f"empty element in {spec!r}")
        if part == "*":
            mins.update(range(60))
        elif part.startswith("*/"):
            step = part[2:]
            if not step.isdigit() or int(step) < 1:
                raise bad(f"step {step!r} is not a positive integer")
            mins.update(range(0, 60, int(step)))
        elif "-" in part:
            halves = part.split("-")
            if len(halves) != 2 or not all(h.isdigit() for h in halves):
                raise bad(f"range {part!r} is not exactly two integers")
            a, b = int(halves[0]), int(halves[1])
            if a > b:
                raise bad(f"range {part!r} is descending, which cron does not accept")
            if b > 59:
                raise bad(f"range {part!r} exceeds minute 59")
            mins.update(range(a, b + 1))
        elif part.isdigit():
            if int(part) > 59:
                raise bad(f"minute {part!r} exceeds 59")
            mins.add(int(part))
        else:
            raise bad(f"element {part!r} is not a minute, a range, or a step")
    if not mins:
        raise bad("it yields no ticks at all")
    return sorted(mins)


def watcher_minutes():
    """(minutes, evidence_line). Raises if the watcher line is absent.

    The two failure modes below are deliberately NOT collapsed (@taOSc-dev, bus 2782).
    An unreadable crontab and a deleted watcher line both yield empty stdout, but they
    call for opposite responses: the first means this script cannot see the schedule,
    the second means the schedule is gone. Reporting "the staleness model is gone" when
    the crontab merely could not be read tells the operator to re-derive a model that is
    fully intact. @taOSc-dev hit exactly this from the other side: `crontab` is not in
    their permission set, so they saw the watcher-is-gone text while the watcher ran fine.
    """
    proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(
            "FAIL: could not READ the crontab (`crontab -l` exited "
            f"{proc.returncode}: {proc.stderr.strip() or 'no stderr'}).\n"
            "This is NOT the same as the watcher being gone, and it does NOT mean the\n"
            "staleness model is invalid. It means THIS PROCESS cannot see the schedule:\n"
            "wrong user, no crontab binary, or a sandboxed/systemd context with no HOME.\n"
            f"Running as user: {getpass.getuser()!r}. The watcher line lives in jay's crontab."
        )
    out = proc.stdout
    matches = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("#"):
            continue
        m = WATCHER_RE.match(line)
        if not m:
            continue
        matches.append((m, line))

    # More than one watcher line is a schedule this script does not model. The union
    # would arm EARLIER than one of them writes, so it is not safely mergeable without
    # knowing which line actually maintains current.json (@taOSc-dev, bus 2785 (C)).
    if len(matches) > 1:
        raise SystemExit(
            f"FAIL: {len(matches)} taos-usage/watch.sh lines in the crontab. This script\n"
            "models exactly one writer and cannot tell which maintains current.json.\n"
            + "".join(f"  {ln}\n" for _, ln in matches) +
            "Resolve to a single watcher line, or teach this script which one wins."
        )

    for m, line in matches:
        spec = m.group(1)
        # Fields 2-5 must be unrestricted. See WATCHER_RE.
        FIELDS = (("hour", m.group(2)), ("day-of-month", m.group(3)),
                  ("month", m.group(4)), ("day-of-week", m.group(5)))
        restricted = [(name, val) for name, val in FIELDS if val != "*"]
        if restricted:
            raise SystemExit(
                "FAIL: the watcher line restricts cron fields this script does not model:\n"
                + "".join(f"  {name} = {val!r}\n" for name, val in restricted) +
                f"  line: {line}\n"
                "Only the MINUTE field is modelled; the tick generator assumes the watcher\n"
                "runs every hour of every day. With a restriction present it would derive an\n"
                "arm time asserting a write that never happens, which is exactly the\n"
                "staleness bug this script exists to prevent. REFUSING to model it wrong."
            )
        return parse_minutes(spec, line), line
    raise SystemExit(
        "FAIL: the crontab WAS read, but it contains no taos-usage/watch.sh line.\n"
        f"(read successfully as user {getpass.getuser()!r}; the watcher line lives in jay's.)\n"
        "REFUSING to guess an offset. The whole point of this script is that the arm\n"
        "time is derived from the watcher's real schedule. If the watcher is genuinely\n"
        "gone, the staleness model is gone too and the caller must re-derive it rather\n"
        "than fall back to a constant."
    )


def first_tick_after(when, minutes, min_lead=MIN_LEAD_SECONDS):
    """First cron tick at least `min_lead` seconds after `when`.

    NOT merely "strictly after". Found by the script's own RED test (2026-08-16):
    under a */15 spec the first tick after a 07:59:59 reset is 08:00:00, i.e. 0.2s
    later, and a watcher firing 0.2s after the reset instant is racing the upstream
    rollover rather than observing it. That is a DIFFERENT failure from the phase bug
    this script was written for, and "strictly after" does not exclude it. Skipping to
    the next tick costs one cadence of latency and removes the race.
    """
    t = when.replace(second=0, microsecond=0)
    for _ in range(60 * 25):  # a day of minutes, far past any real gap
        t += datetime.timedelta(minutes=1)
        if t.minute in minutes and (t - when).total_seconds() >= min_lead:
            return t
    raise SystemExit("FAIL: no tick found within 24h of the reset.")


def fire_time(tick, margin):
    """The instant cron ACTUALLY fires for a given tick and margin.

    CEIL to the next whole minute. cron has minute resolution, so truncating here
    silently DISCARDS the margin: a 30s margin printed an ARM AT of :06:30 and a cron
    line of :06, which fires AT THE TICK with an effective margin of 0s against a
    ~2.3s write. The two lines contradicted each other on the same run
    (@taOSc-dev, bus 2782; reproduced before fixing). Rounding down is the one
    direction that reintroduces the exact race the margin exists to prevent.

    Factored out so the PRIMARY and the RETRY cannot round differently. Two copies of
    a rounding rule is the duplicate-definition trap of tsk-djcab7: both would be
    individually correct and no gate we run would see them diverge.
    """
    arm = tick + datetime.timedelta(seconds=margin)
    fire = arm.replace(second=0, microsecond=0)
    if arm.second or arm.microsecond:
        fire += datetime.timedelta(minutes=1)
    return arm, fire


def cron_fields(fire):
    """The four numeric fields this script emits, from an aware instant."""
    local = fire.astimezone()
    return local.minute, local.hour, local.day, local.month


def check_round_trip(fire, label):
    """Refuse to emit a cron line that does not denote the instant it was derived from.

    (@taOSc-dev, bus 2790 (B).) The UTC->local CONVERSION was already correct; the
    REPRESENTATION is what leaks. A local wall clock is not a bijection with instants:

      - DST fall-back. In the repeated hour the emitted line denotes TWO instants and
        cron fires the EARLIER one, so the wake lands an hour BEFORE the tick it was
        derived from. That is the original staleness bug with a correct derivation
        sitting upstream of it, and early is the dangerous direction.
      - DST spring-forward. In the skipped hour the line denotes NO instant.

    MEASURED before fixing, with TZ=America/New_York and a reset inside the 2026-11-01
    fold: derived fire 06:07:00Z, emitted line `7 1 1 11`, which denotes 05:07:00Z.
    Sixty minutes early. This box is UTC so it round-trips today BY INSTANCE, in exactly
    the way `+11` was right by instance.

    The check models none of that. It rebuilds the instant from the four fields actually
    printed and compares, without knowing what DST is: REFUSE WHAT YOU DO NOT MODEL,
    applied to output instead of input.

    WHAT IT ACTUALLY COVERS, corrected after writing the test rather than claimed from
    the design: the FOLD, yes, measured above. The GAP, no - and it cannot, from this
    direction. `fire.astimezone()` always yields a wall clock that EXISTS, so no derived
    instant can land in the skipped hour, and the spring-forward case is unreachable here
    rather than caught. The docstring said "the fold, the gap, and any dropped field"
    until the test showed otherwise. A guard advertising coverage it does not have is the
    same defect as a gate whose PASS is reachable from zero data.

    NOT CAUGHT, and stated rather than papered over: a 5-field cron line carries no YEAR,
    so this cannot detect a line left in place until next year. The one-shot semantics
    live at the scheduler layer (CronCreate with recurring=false), not in these five
    fields, so the representation is only complete in company with that flag.
    """
    err = round_trip_error(fire, label)
    if err:
        raise SystemExit(err)


def round_trip_error(fire, label):
    """None if the emitted fields denote `fire`, else the reason as a string."""
    mi, ho, dy, mo = cron_fields(fire)
    local = fire.astimezone()
    # fold=0 is deliberate: it is the EARLIER of a repeated wall clock, which is the one
    # cron fires. Reconstructing with fold=1 would hide the very failure being checked.
    rebuilt = datetime.datetime(local.year, mo, dy, ho, mi)
    denoted = rebuilt.astimezone()
    if denoted != fire:
        drift = (denoted - fire).total_seconds() / 60
        # NAME THE NONEXISTENT WALL CLOCK RATHER THAN REPORTING IT AS DRIFT
        # (@taOSc-dev, bus 2795 (1)). If a field were dropped or mutated the rebuilt wall
        # clock can be one that does not exist (the spring-forward gap). Python does not
        # raise there, it normalises off the pre-transition offset, so the refusal is still
        # correct but would be REPORTED as an hour of drift - the right refusal wearing a
        # misleading reason, which is the shape of the margin=0 test I had to fix.
        # A wall clock exists iff rendering its own denoted instant back reproduces it.
        exists = denoted.replace(tzinfo=None) == rebuilt
        cause = ("that wall clock DOES NOT EXIST in this zone (a spring-forward gap); it was\n"
                 "normalised onto the pre-transition offset rather than rejected"
                 if not exists else
                 "that wall clock is AMBIGUOUS in this zone (a fall-back fold): it denotes two\n"
                 "instants and cron fires the earlier")
        return (
            f"FAIL: the {label} cron line `{mi} {ho} {dy} {mo} *` does not denote the\n"
            f"instant it was derived from.\n"
            f"  derived : {fire.isoformat()}  ({fire.astimezone(datetime.timezone.utc)} UTC)\n"
            f"  denotes : {denoted.isoformat()}  "
            f"({denoted.astimezone(datetime.timezone.utc)} UTC)\n"
            f"  drift   : {drift:+.0f} min\n"
            f"  cause   : {cause}\n"
            f"Local zone {time.tzname} does not render this instant and that wall clock as a\n"
            "pair, so the four fields cannot carry it. REFUSING to emit this line."
        )


def unsafe_reason(tick, margin):
    """None if a wake armed off `tick` is safe to emit, else why not.

    One predicate for every way an arm can be wrong, so the primary and the retry cannot
    disagree about what "safe" means. Three ways, and they come from three different
    layers, which is the whole lesson of this file:
      1. the ARITHMETIC layer  - the ceil could put the fire at or before the tick;
      2. the SCHEDULER layer   - a one-shot on :00/:30 fires up to 90s early, which can
                                 undo a margin that only ever declared the watcher;
      3. the REPRESENTATION    - the four emitted fields may not denote the instant.
    """
    arm, fire = fire_time(tick, margin)
    if fire <= tick:
        return arm, fire, (f"fire {fire.isoformat()} is not after tick {tick.isoformat()}")
    lead = (fire - tick).total_seconds()
    if fire.astimezone().minute in JITTERED_MINUTES and lead <= SCHEDULER_EARLY_JITTER_SECONDS:
        return arm, fire, (
            f"fires on local minute :{fire.astimezone().minute:02d}, where the scheduler "
            f"fires one-shots up to {SCHEDULER_EARLY_JITTER_SECONDS}s EARLY, which exceeds "
            f"this arm's {lead:.0f}s lead over its tick and can land it BEFORE the write")
    err = round_trip_error(fire, "candidate")
    if err:
        return arm, fire, "the emitted fields do not denote it (DST); " + err.splitlines()[0]
    return arm, fire, None


def next_safe_tick(after, minutes, margin, min_lead, skips):
    """First tick at least `min_lead` after `after` whose ARM is safe to emit.

    ADVANCING RATHER THAN REFUSING (@taOSc-dev, bus 2795 (2)). Refusing on the DST fold
    was correct but incomplete: it turns "fires 60 minutes early" into "does not fire at
    all", which is a strict improvement and still not a working wake, and on those two
    days a year the resume arm simply would not be scheduled. Every unsafe condition here
    is a property of a PARTICULAR tick, so the next one is usually fine: advancing costs
    a bounded amount of latency and produces a wake. Skips are recorded and PRINTED, never
    silent - a tool that quietly moved the wake would be worse than one that refused.
    """
    t = after.replace(second=0, microsecond=0)
    for _ in range(60 * 25):
        t += datetime.timedelta(minutes=1)
        if t.minute not in minutes or (t - after).total_seconds() < min_lead:
            continue
        arm, fire, why = unsafe_reason(t, margin)
        if why is None:
            return t, arm, fire
        skips.append((t, why))
    raise SystemExit(
        "FAIL: no tick within 24h yields a safe arm.\n"
        + "".join(f"  skipped {t.isoformat()}: {w}\n" for t, w in skips[:8]) +
        f"REFUSING to emit any of them.\n"
        f"REMEDY when every skip cites the scheduler's early-fire jitter: raise the margin "
        f"so the\narm clears the jittered minute. A watcher on :29/:59 with the default "
        f"{MARGIN_SECONDS}s lands\nevery arm on :30/:00; margin 120 moves them to :31/:01 "
        f"and derives normally. Pass it as\nargv[2]. The margin is a floor on the lead, not "
        f"a target, so raising it costs only latency.")


def retry_after(primary_fire, minutes, margin, lead=RETRY_LEAD_SECONDS):
    """First tick whose FIRE lands at least `lead` seconds after the primary FIRES.

    WHY THE RETRY IS DERIVED TOO (tsk-fd3kes, 2026-08-16). The primary became a
    function of the watcher's cron spec while the retry stayed a flat resets_at+22min.
    That is not merely inelegant, it silently re-creates the ORIGINAL bug: widen the
    watcher to */30 and the derived primary moves to 08:31 while the retry sits at
    08:21, so THE RETRY FIRES FIRST. The safety net becomes the primary, at a time
    derived from nothing, i.e. inside the stale window - and it looks fine, because
    the primary next to it is visibly derived. RED-tested: */30 inverts under the flat
    rule and does not under this one.

    The constraint is stated on the FIRE times rather than the ticks, because the
    ceil-to-minute is what cron actually obeys and it can move a tick across the bound.

    Note this composes TWO dependencies without conflating them: WHICH INSTANTS are
    available comes from the watcher's cron spec (so the retry, like the primary, reads
    a freshly-written file), while HOW LONG TO WAIT comes from the primary's
    fire-to-delete latency. Neither is the other's function.
    """
    t = primary_fire
    for _ in range(60 * 25):  # a day of minutes, far past any real gap
        t += datetime.timedelta(minutes=1)
        if t.minute not in minutes:
            continue
        arm, fire, why = unsafe_reason(t, margin)
        if why is None and (fire - primary_fire).total_seconds() >= lead:
            return t, arm, fire
    raise SystemExit(
        f"FAIL: no watcher tick within 24h of the primary fire {primary_fire.isoformat()} "
        f"leaves the required {lead}s retry lead. REFUSING to emit an unordered pair."
    )


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    raw = sys.argv[1]
    try:
        resets_at = datetime.datetime.fromisoformat(raw)
    except ValueError as e:
        raise SystemExit(
            f"FAIL: could not parse {raw!r} as an ISO 8601 timestamp ({e}).\n"
            "Note a trailing 'Z' only parses on Python 3.11+; on older interpreters pass\n"
            "'+00:00' instead. Running: " + sys.version.split()[0]
        )
    # REJECT naive input rather than assuming UTC. The old `.replace(tzinfo=utc)` was an
    # unnamed assumption of exactly the kind this file exists to eliminate: it is correct
    # only because this box is UTC, and a caller passing naive LOCAL time on any other host
    # would shift the whole derivation by the offset with no constant appearing to change
    # (@taOSc-dev, bus 2785 (B)). The output side already learned to state its timezone;
    # this is the input side learning the same thing.
    if resets_at.tzinfo is None:
        raise SystemExit(
            f"FAIL: {raw!r} has no timezone. REFUSING to assume UTC.\n"
            "This script emits a LOCAL cron line from a UTC-anchored computation, so an\n"
            "ambiguous input silently shifts every armed wake by the host's offset.\n"
            "Pass an explicit offset, e.g. '2026-08-16T07:59:59+00:00'. The usage API's\n"
            "resets_at already carries one."
        )
    # argv[2] gets the SAME named-failure treatment as argv[1] (@taOSc-dev, bus 2790 (C)).
    # It was a bare `int(sys.argv[2])` three lines under the block that catches argv[1] and
    # names the interpreter version: `... abc` was an unhandled ValueError traceback, and a
    # NEGATIVE margin was caught only by the `fire <= tick` assert, so the operator passing
    # -30 was told "computed fire is not after tick" - the right refusal for the wrong
    # reason, which is the same shape as (A). Reproduced for 'abc', '1e3', '' and '-30'
    # before fixing.
    if len(sys.argv) > 2:
        raw_margin = sys.argv[2]
        if not raw_margin.lstrip("-").isdigit():
            raise SystemExit(
                f"FAIL: margin {raw_margin!r} is not an integer number of seconds.\n"
                "It is argv[2] and it is optional; omit it to use the default "
                f"{MARGIN_SECONDS}s.\n"
                "Note '1e3' and '60.0' are REFUSED on purpose rather than coerced: a margin\n"
                "is the one knob that decides whether the armed wake clears the watcher's\n"
                "write, so it is not a place to guess what the caller meant."
            )
        margin = int(raw_margin)
        if margin < 1:
            raise SystemExit(
                f"FAIL: margin {margin}s is not positive.\n"
                "The margin exists to clear the watcher's write, which lands ~2.3s after\n"
                "its tick. A zero or negative margin arms AT or BEFORE the tick, which is\n"
                "precisely the race this script was written to remove.\n"
                "(Previously this was caught only by the fire-vs-tick assert, which told\n"
                "you the computed fire was wrong rather than that YOUR INPUT was.)"
            )
    else:
        margin = MARGIN_SECONDS

    minutes, evidence = watcher_minutes()
    skips = []
    tick, arm, fire = next_safe_tick(resets_at, minutes, margin, MIN_LEAD_SECONDS, skips)

    # cron reads LOCAL time; every instant above is UTC. This box is UTC so the two
    # coincided, which made the dependency invisible. Convert explicitly and print the
    # zone, so the cron line is correct by construction rather than by instance.
    fire_local = fire.astimezone()

    # `or 60` because a wrap-around gap of 0 means a SINGLE tick per hour, i.e. the
    # widest cadence, and reporting it as the narrowest inverts the evidence line.
    gaps = [((minutes[(i + 1) % len(minutes)] - minutes[i]) % 60) or 60
            for i in range(len(minutes))]

    if fire <= tick:
        raise SystemExit(f"FAIL: computed fire {fire.isoformat()} is not after tick "
                         f"{tick.isoformat()}. Refusing to emit a racing cron line.")

    print("EVIDENCE (read, not assumed):")
    print("  crontab: " + evidence)
    print(f"  read as user={getpass.getuser()!r}  local_tz={time.tzname}  "
          f"utc_offset={fire_local.strftime('%z')}")
    print(f"  ticks={minutes}  gaps={gaps}  uniform={len(set(gaps)) == 1}  max_gap={max(gaps)}")
    print(f"  margin={margin}s (fn of watcher write duration, MEASURED ~2.3s)  "
          f"min_lead={MIN_LEAD_SECONDS}s (fn of upstream rollover, UNMEASURED)")
    for t, why in skips:
        print(f"SKIPPED    {t.isoformat()}: {why}")
    print(f"RESET      {resets_at.isoformat()}")
    print(f"FIRST TICK {tick.isoformat()}   (>= {MIN_LEAD_SECONDS}s after the reset)")
    print(f"ARM AT     {arm.isoformat()}   (tick + {margin}s margin)")
    print(f"FIRES AT   {fire.isoformat()}   (ceiled to the minute; cron has no seconds)")
    print(f"           {fire_local.isoformat()}   (LOCAL, which is what cron reads)")
    print(f"EFFECTIVE  {(fire - tick).total_seconds():.0f}s after the tick   "
          f"(this, not the margin, is what actually protects the read)")
    print(f"WAIT       {(fire - resets_at).total_seconds() / 60:.1f} min after reset")
    check_round_trip(fire, "PRIMARY")
    print("CRON       " + "{} {} {} {} *".format(*cron_fields(fire)))

    # THE RETRY, derived from the same crontab read as the primary. Deriving it in a
    # SECOND invocation would leave the pair's ordering resting on the crontab not
    # changing in between, which is an unnamed dependency of exactly the kind this file
    # exists to remove. One read, one ordering, both lines.
    r_tick, r_arm, r_fire = retry_after(fire, minutes, margin)
    r_local = r_fire.astimezone()

    # The pair MUST be ordered. With RETRY_LEAD_SECONDS at 900 this is implied, but the
    # assert is the thing that survives someone editing the constant, and an unordered
    # pair is silent: both crons exist, both fire, and the wake still appears to work.
    if r_fire <= fire:
        raise SystemExit(
            f"FAIL: retry {r_fire.isoformat()} does not fire after primary "
            f"{fire.isoformat()}. REFUSING to emit an inverted pair: the retry would "
            "become the primary, at a time derived from nothing."
        )

    print(f"RETRY TICK {r_tick.isoformat()}   (first tick clearing the lead)")
    print(f"RETRY ARM  {r_arm.isoformat()}   (tick + {margin}s margin)")
    print(f"RETRY FIRE {r_fire.isoformat()}   (ceiled; same rounding as the primary)")
    print(f"           {r_local.isoformat()}   (LOCAL, which is what cron reads)")
    print(f"RETRY GAP  {(r_fire - fire).total_seconds() / 60:.1f} min after the PRIMARY   "
          f"(lead={RETRY_LEAD_SECONDS}s, fn of the primary's fire-to-delete latency, "
          "MEASURED)")
    print(f"RETRY WAIT {(r_fire - resets_at).total_seconds() / 60:.1f} min after reset   "
          "(was a flat 22; it is no longer a constant)")
    check_round_trip(r_fire, "RETRY")
    print("RETRY CRON " + "{} {} {} {} *".format(*cron_fields(r_fire)))
    print("ONE-SHOT   both lines carry NO YEAR (5-field cron has no such field), so they\n"
          "           are only one-shots in company with the scheduler's recurring=false.\n"
          "           Left recurring, each re-fires on this day next year.")
    # DURABILITY, and it is the half the ONE-SHOT line above does NOT cover
    # (@taOSc-dev, bus 2798 (1)). A reader who supplies recurring=false correctly still
    # gets an arm that dies at session exit, and nothing printed said so. CronCreate:
    # "All jobs are session-only (in-memory, gone when this Claude session ends)."
    # For a RESUME pair that is the failure domain, not a footnote: the retry exists to
    # cover a primary that did not fire, and if the primary did not fire BECAUSE the
    # session ended, the retry ended with it. The safety net is stored inside the thing
    # it is meant to survive. VERIFIED for this seat 2026-08-16: all four jobs list as
    # [session-only] and `crontab -l` contains no resume line (positive control: the
    # crontab reads fine, 28 lines, and the backup watcher is on line 8).
    print("DURABILITY if armed via CronCreate these are SESSION-ONLY: in-memory, gone when\n"
          "           the session ends. THE RETRY THEREFORE CANNOT COVER SESSION DEATH - it\n"
          "           dies with the primary it is insuring. It covers only a wake that failed\n"
          "           while the session lived. Session death is covered, if at all, by\n"
          "           something in the SYSTEM crontab, outside the component that failed.\n"
          "           If instead you write these lines into a crontab, durability is fine but\n"
          "           recurring=false does not exist there, so re-read the ONE-SHOT line.")


if __name__ == "__main__":
    main()
