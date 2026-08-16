#!/usr/bin/env python3
"""Surface-enumerating tests for resume_arm_time.py.  Run: python3 test_resume_arm_time.py

WHY THIS FILE EXISTS, and why it is shaped this way (@taOSc-dev, bus 2785).

Four review rounds found nine defects in this helper. Every one was found by an
adversarial READER, none by my own RED tests. I concluded that argued for review over
tests. That was the wrong half of a true observation, and the rebuttal is the reason
this file exists:

  Review is not durable. "Get an adversarial reader" makes correctness a function of
  one agent's availability, which is an UNNAMED DEPENDENCY AT THE SITE - the exact
  finding this helper was rewritten four times to eliminate. It would not be accepted
  anywhere else in the file, so it is not accepted here.

The other half: my RED tests were not weak because they were self-written, they were
weak because of what GENERATED them. They enumerated the scenarios I already suspected
(missing watcher, widened cadence), so they encoded the same model as the code. Every
defect the reviewer found came from a different generator, and a mechanical one:

  ENUMERATE THE SURFACE THE CODE EXPOSES, NOT THE SCENARIOS YOU SUSPECT.

The surface is defined by the code, not by anyone's model of the input. That is why it
finds things the author's imagination does not. Concretely, one test per:
  - command-line ARGUMENT, including its degenerate values
  - BRANCH of every parse
  - FIELD of every subprocess result
  - FIELD that gets PRINTED (the evidence lines are output, so they are surface)

So: a spec the script does not model must be REJECTED, and that test is written by
reading the regex, not by predicting what the crontab will say.
"""
import datetime
import importlib.util
import os
import subprocess
import sys

spec = importlib.util.spec_from_file_location("m", os.path.join(os.path.dirname(os.path.abspath(__file__)), "resume_arm_time.py"))
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)

RESET = "2026-08-16T07:59:59+00:00"
GOOD = "6,16,26,36,46,56 * * * * /usr/bin/bash /home/jay/.taos-usage/watch.sh --once\n"

FAILS = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


class Proc:
    def __init__(self, rc, out, err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def with_crontab(text, rc=0):
    M.subprocess.run = lambda *a, **k: Proc(rc, text)


def run_cli(args, crontab=GOOD, rc=0):
    """Run main() as the CLI does, capturing stdout / SystemExit."""
    import io
    import contextlib
    with_crontab(crontab, rc)
    old = sys.argv
    sys.argv = ["resume_arm_time.py"] + args
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            M.main()
        return 0, buf.getvalue()
    except SystemExit as e:
        return 1, str(e) if not isinstance(e.code, int) else buf.getvalue()
    finally:
        sys.argv = old


print("SURFACE: subprocess result fields")
rc, out = run_cli([RESET], crontab="", rc=1)
check("crontab unreadable (returncode != 0) is rejected", rc == 1 and "could not READ" in out)
check("  ...and is NOT diagnosed as watcher-gone", "staleness model is gone" not in out)
rc, out = run_cli([RESET], crontab="17 */2 * * * /other/thing.py\n")
check("crontab readable but no watcher line is rejected", rc == 1 and "WAS read" in out)
check("  ...with a DIFFERENT diagnosis from the unreadable case", "could not READ" not in out)
rc, out = run_cli([RESET], crontab=GOOD + "3,33 * * * * /bin/bash /home/jay/.taos-usage/watch.sh --once\n")
check("two watcher lines are rejected, not silently first-wins", rc == 1 and "models exactly one writer" in out)

print("SURFACE: the five cron fields (one test per field)")
for field, line in [
    ("hour", "6,16 8 * * * /bin/bash /home/jay/.taos-usage/watch.sh\n"),
    ("day-of-month", "6,16 * 3 * * /bin/bash /home/jay/.taos-usage/watch.sh\n"),
    ("month", "6,16 * * 4 * /bin/bash /home/jay/.taos-usage/watch.sh\n"),
    ("day-of-week", "6,16 * * * 1-5 /bin/bash /home/jay/.taos-usage/watch.sh\n"),
]:
    rc, out = run_cli([RESET], crontab=line)
    check(f"restricted {field} is REFUSED (not modelled)", rc == 1 and field in out)
rc, out = run_cli([RESET], crontab=GOOD)
check("all-wildcard fields 2-5 are accepted", rc == 0)

print("SURFACE: minute-field parse branches")
for spec_txt, want in [("*/15", 0), ("6-9", 0), ("6,16,26,36,46,56", 0), ("*", 0)]:
    rc, out = run_cli([RESET], crontab=f"{spec_txt} * * * * /bin/bash /home/jay/.taos-usage/watch.sh\n")
    check(f"minute spec {spec_txt!r} parses", rc == want)

print("SURFACE: argv[1] resets_at")
rc, out = run_cli(["2026-08-16T07:59:59"])
check("NAIVE timestamp is REFUSED, not assumed UTC", rc == 1 and "REFUSING to assume UTC" in out)
rc, out = run_cli(["not-a-timestamp"])
check("unparseable timestamp fails with a named reason", rc == 1 and "ISO 8601" in out)

print("SURFACE: argv[2] margin (the knob no earlier test ever varied)")
rc, out = run_cli([RESET, "0"])
# The refusal was always correct; it used to come from the fire-vs-tick assert, so it
# blamed the COMPUTED FIRE for what was an INPUT error (@taOSc-dev, bus 2790 (C)).
# Now it names the argument. Pinning the reason, not just the exit.
check("margin=0 is REFUSED (arming at the tick races the write)",
      rc == 1 and "is not positive" in out)
check("  ...and blames the INPUT, not the computed fire", "not after tick" not in out)
for margin in ["1", "30", "59", "60", "61", "600"]:
    rc, out = run_cli([RESET, margin])
    tick = [l for l in out.splitlines() if l.startswith("FIRST TICK")][0].split()[2]
    fire = [l for l in out.splitlines() if l.startswith("FIRES AT")][0].split()[2]
    eff = int([l for l in out.splitlines() if l.startswith("EFFECTIVE")][0].split()[1].rstrip("s"))
    t = datetime.datetime.fromisoformat(tick)
    f = datetime.datetime.fromisoformat(fire)
    check(f"margin={margin}: fires STRICTLY after the tick", f > t, f"tick={tick} fire={fire}")
    check(f"margin={margin}: effective margin is never 0", eff > 0, f"effective={eff}s")
    check(f"margin={margin}: effective covers the measured ~2.3s write", eff >= 3, f"effective={eff}s")

print("SURFACE: every printed evidence field")
rc, out = run_cli([RESET])
for field in ["EVIDENCE", "crontab:", "read as user=", "local_tz=", "ticks=", "gaps=",
              "max_gap=", "margin=", "min_lead=", "RESET", "FIRST TICK", "ARM AT",
              "FIRES AT", "EFFECTIVE", "WAIT", "CRON"]:
    check(f"prints {field!r}", field in out)
cron = [l for l in out.splitlines() if l.startswith("CRON")][0].split()
fire_local = [l for l in out.splitlines() if l.strip().startswith("2026") and "LOCAL" not in l]
check("CRON minute matches the FIRES AT local minute",
      cron[1] == str(datetime.datetime.fromisoformat(
          [l for l in out.splitlines() if l.startswith("FIRES AT")][0].split()[2]
      ).astimezone().minute))

print("SURFACE: gaps evidence for degenerate tick sets")
rc, out = run_cli([RESET], crontab="6 * * * * /bin/bash /home/jay/.taos-usage/watch.sh\n")
check("single-tick spec reports max_gap=60, not 0", "max_gap=60" in out,
      [l for l in out.splitlines() if "gaps=" in l][0].strip())


# ---------------------------------------------------------------------------
# THE RETRY (tsk-fd3kes). Same generator as everything above: enumerate what the
# new code EXPOSES - one constant, two functions with three branches between them,
# seven printed fields, and one ordering assert - not the ways I imagine it failing.
# ---------------------------------------------------------------------------

def lines(out):
    return {l.split("  ")[0].strip(): l for l in out.splitlines()}


def field(out, prefix):
    return [l for l in out.splitlines() if l.startswith(prefix)][0]


def stamp(out, prefix):
    return datetime.datetime.fromisoformat(field(out, prefix).split()[2])


print("SURFACE: every printed RETRY field")
rc, out = run_cli([RESET])
for f in ["RETRY TICK", "RETRY ARM", "RETRY FIRE", "RETRY GAP", "RETRY WAIT", "RETRY CRON"]:
    check(f"prints {f!r}", f in out)
check("RETRY CRON minute matches the RETRY FIRE local minute",
      field(out, "RETRY CRON").split()[2] == str(stamp(out, "RETRY FIRE").astimezone().minute))

print("SURFACE: the two REPRESENTATION-COMPLETENESS lines (both are printed output)")
rc, out = run_cli([RESET])
check("prints ONE-SHOT (the year is not in the five fields)", "ONE-SHOT" in out)
check("prints DURABILITY (@taOSc-dev bus 2798: a correct recurring=false still dies "
      "at session exit)", "DURABILITY" in out)
check("  ...and says the retry cannot cover SESSION DEATH", "CANNOT COVER SESSION DEATH" in out)
check("  ...and points at the SYSTEM crontab as the layer that can", "SYSTEM crontab" in out)

print("SURFACE: the retry is DERIVED, not an offset (the whole point of the card)")
rc, out = run_cli([RESET])
ticks = eval(field(out, "  ticks=").split("ticks=")[1].split("  ")[0])
check("RETRY TICK is one of the watcher's real ticks", stamp(out, "RETRY TICK").minute in ticks,
      f"retry tick minute={stamp(out, 'RETRY TICK').minute} ticks={ticks}")
check("RETRY FIRE is strictly after RETRY TICK (margin not truncated away)",
      stamp(out, "RETRY FIRE") > stamp(out, "RETRY TICK"))
check("RETRY WAIT is no longer the flat 22", "22.0 min after reset" not in field(out, "RETRY WAIT"))

print("SURFACE: ordering across EVERY minute-spec parse branch (RED case included)")
for spec_txt in ["*/30", "*/15", "6,16,26,36,46,56", "6-9", "*", "6", "30"]:
    rc, out = run_cli([RESET], crontab=f"{spec_txt} * * * * /bin/bash /home/jay/.taos-usage/watch.sh\n")
    p, r = stamp(out, "FIRES AT"), stamp(out, "RETRY FIRE")
    check(f"spec {spec_txt!r}: retry fires AFTER primary", r > p,
          f"primary={p:%H:%M} retry={r:%H:%M}")
    check(f"spec {spec_txt!r}: retry lands on a real tick",
          stamp(out, "RETRY TICK").minute in eval(field(out, "  ticks=").split("ticks=")[1].split("  ")[0]))
    check(f"spec {spec_txt!r}: gap is at least the declared lead",
          (r - p).total_seconds() >= M.RETRY_LEAD_SECONDS,
          f"gap={(r - p).total_seconds():.0f}s lead={M.RETRY_LEAD_SECONDS}s")

print("SURFACE: the RED case this card was filed for")
rc, out = run_cli([RESET], crontab="*/30 * * * * /bin/bash /home/jay/.taos-usage/watch.sh\n")
p, r = stamp(out, "FIRES AT"), stamp(out, "RETRY FIRE")
flat22 = datetime.datetime.fromisoformat(RESET) + datetime.timedelta(minutes=22)
check("under */30 the OLD flat +22 retry WOULD have inverted (the defect is real)", flat22 < p,
      f"flat+22={flat22:%H:%M} primary={p:%H:%M}")
check("  ...and the derived retry does NOT invert", r > p, f"primary={p:%H:%M} retry={r:%H:%M}")

print("SURFACE: argv[2] margin interacts with BOTH halves of the pair")
for margin in ["1", "30", "59", "60", "61", "600"]:
    rc, out = run_cli([RESET, margin])
    p, r = stamp(out, "FIRES AT"), stamp(out, "RETRY FIRE")
    check(f"margin={margin}: pair stays ordered", r > p, f"primary={p:%H:%M} retry={r:%H:%M}")
    check(f"margin={margin}: retry fires strictly after its own tick",
          r > stamp(out, "RETRY TICK"))

print("SURFACE: fire_time is SHARED, so the two halves cannot round differently")
for margin in [1, 30, 59, 60, 61, 600]:
    t = datetime.datetime.fromisoformat("2026-08-16T08:06:00+00:00")
    a1, f1 = M.fire_time(t, margin)
    a2, f2 = M.fire_time(t, margin)
    check(f"fire_time margin={margin} is deterministic and ceils", f1 >= a1 and f1 == f2,
          f"arm={a1:%H:%M:%S} fire={f1:%H:%M}")

print("SURFACE: retry_after branches")
mins = [6, 16, 26, 36, 46, 56]
pf = datetime.datetime.fromisoformat("2026-08-16T08:07:00+00:00")
tk, ar, fr = M.retry_after(pf, mins, 60, lead=1)
check("lead=1 still skips to a real tick (not primary+1s)", fr > pf and tk.minute in mins,
      f"tick={tk:%H:%M} fire={fr:%H:%M}")
# The loop scans 60*25 minutes, so a lead of EXACTLY 25h is still satisfiable on the
# last minute it examines. Found by writing this test with 25h and watching it not
# raise. Both sides of that boundary are pinned, because the interesting thing about a
# bound is where it stops being one, and a test that only probes the safe side would
# have recorded the wrong number as the cliff.
tk, ar, fr = M.retry_after(pf, mins, 60, lead=60 * 60 * 25)
check("lead of exactly 25h is still satisfiable (the loop's last minute)",
      (fr - pf).total_seconds() >= 60 * 60 * 25, f"gap={(fr - pf).total_seconds():.0f}s")
try:
    M.retry_after(pf, mins, 60, lead=60 * 60 * 26)
    check("lead beyond the scan window raises rather than returning an unordered pair", False)
except SystemExit as e:
    check("lead beyond the scan window raises rather than returning an unordered pair",
          "REFUSING to emit an unordered pair" in str(e))


# ---------------------------------------------------------------------------
# THE SECOND HALF OF THE RULE (@taOSc-dev, bus 2790). Enumerating the surface is
# not enough if every element is enumerated to its PLAUSIBLE value. My margin set
# was {0,1,30,59,60,61,600} and the single degenerate member found the single bug;
# my minute-spec set was four specs the parser MODELS and none it merely ADMITS.
# The code was blind and the suite was blind in the same shape, because one
# enumeration generated both. Degenerate values are mechanical too: for an int
# argument they are 0, negative and non-numeric; for a regex-admitted field they
# are the strings the regex accepts and the parser does not.
# ---------------------------------------------------------------------------

print("SURFACE: minute specs WATCHER_RE admits but parse_minutes does not model")
for bad_spec in ["5-2", "*/0", "-", ",,", "1--2", "99", "6-99", "*/", "1-2-3"]:
    rc, out = run_cli([RESET], crontab=f"{bad_spec} * * * * /bin/bash /home/jay/.taos-usage/watch.sh\n")
    check(f"spec {bad_spec!r} is REFUSED by name", rc == 1 and "is not one" in out,
          out.strip().splitlines()[0][:60] if out.strip() else "(empty)")
    check(f"  ...NOT misdiagnosed as watcher-gone", "no taos-usage/watch.sh line" not in out)
    check(f"  ...and not a bare traceback", "Traceback" not in out and rc == 1)
for good_spec in ["*", "6", "6,16", "6-9", "*/15", "0-59", "*/1"]:
    rc, out = run_cli([RESET], crontab=f"{good_spec} * * * * /bin/bash /home/jay/.taos-usage/watch.sh\n")
    check(f"POSITIVE CONTROL: modelled spec {good_spec!r} still derives", rc == 0,
          "" if rc == 0 else out.strip().splitlines()[0][:60])

print("SURFACE: argv[2] margin DEGENERATE values (not just plausible ones)")
for bad_margin, want in [("abc", "not an integer"), ("1e3", "not an integer"),
                         ("", "not an integer"), ("60.0", "not an integer"),
                         ("-30", "not positive"), ("0", "not positive")]:
    rc, out = run_cli([RESET, bad_margin])
    check(f"margin={bad_margin!r} refused with the RIGHT reason", rc == 1 and want in out,
          out.strip().splitlines()[0][:60] if out.strip() else "(empty)")
    check(f"  ...names the INPUT, not the computed fire", "not after tick" not in out)

print("SURFACE: the emitted cron line must DENOTE the derived instant (round-trip)")
import os
import time as _time


def with_tz(tz, fn):
    old = os.environ.get("TZ")
    os.environ["TZ"] = tz
    _time.tzset()
    try:
        return fn()
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        _time.tzset()


# US fall-back 2026-11-01: 02:00 EDT -> 01:00 EST, so local 01:00-01:59 occurs TWICE
# and cron fires the earlier. MEASURED before the fix: the line denoted an instant
# 60 minutes early, i.e. BEFORE the tick it was derived from.
# CHANGED (@taOSc-dev, bus 2795 (2)): the fold used to be REFUSED. Refusing turned
# "fires 60 min early" into "does not fire at all", which on those two days a year meant
# no resume arm was scheduled. Unsafety is a property of a PARTICULAR tick, so it now
# ADVANCES to the next encodable one and PRINTS every skip. Asserting the stronger
# property: it emits, AND what it emits denotes the instant it derived.
rc, out = with_tz("America/New_York",
                  lambda: run_cli(["2026-11-01T06:00:30+00:00"]))
check("DST fall-back fold now ADVANCES and still emits a wake", rc == 0,
      out.strip().splitlines()[0][:60] if out.strip() else "(empty)")
check("  ...skipping the unencodable ticks LOUDLY, never silently", "SKIPPED" in out)
check("  ...and the skip names DST as the reason", "do not denote" in out)


def denotes(out, fire_prefix, cron_prefix):
    """Rebuild the instant from the fields actually printed, as cron would read them."""
    f = stamp(out, fire_prefix)
    c = field(out, cron_prefix).split()
    n = 1 if cron_prefix == "CRON" else 2
    mi, ho, dy, mo = int(c[n]), int(c[n + 1]), int(c[n + 2]), int(c[n + 3])
    return datetime.datetime(f.astimezone().year, mo, dy, ho, mi).astimezone(), f


for fp, cp in [("FIRES AT", "CRON"), ("RETRY FIRE", "RETRY CRON")]:
    den, f = with_tz("America/New_York", lambda: denotes(
        with_tz("America/New_York", lambda: run_cli(["2026-11-01T06:00:30+00:00"]))[1], fp, cp))
    check(f"  ...and the advanced {cp} line DENOTES its own {fp} exactly", den == f,
          f"denotes={den} fire={f}")
rc, out = with_tz("UTC", lambda: run_cli([RESET]))
check("POSITIVE CONTROL: a UTC box round-trips and still emits", rc == 0)
rc, out = with_tz("Asia/Tokyo", lambda: run_cli([RESET]))
check("POSITIVE CONTROL: a non-UTC zone with no DST still emits", rc == 0)
# The spring-forward GAP is NOT reachable from this direction and the test says so
# rather than pretending to cover it: `fire.astimezone()` always yields a wall clock
# that EXISTS, so no derived instant can land in the skipped hour. Recorded because a
# test suite claiming coverage it does not have is the thing this file is against.
rc, out = with_tz("America/New_York", lambda: run_cli(["2026-03-08T06:30:00+00:00"]))
check("spring-forward window: derivation still round-trips (gap unreachable here)",
      rc == 0, "documented as unreachable, not as covered")

print("SURFACE: the SCHEDULER layer, which MARGIN_SECONDS never declared")
# CronCreate: "one-shot tasks landing on :00 or :30 fire up to 90 s early". The resume
# pair ARE one-shots, so a 60s margin on those two minutes can fire 30s BEFORE its tick,
# which is the race the margin exists to prevent, reintroduced by a layer the margin did
# not name. Found by reading the scheduler contract, not by imagining a case.
rc, out = run_cli([RESET], crontab="29,59 * * * * /bin/bash /home/jay/.taos-usage/watch.sh\n")
check("a spec that arms ONLY on :30/:00 is refused, not armed into the jitter",
      rc == 1 and "90s EARLY" in out,
      out.strip().splitlines()[0][:60] if out.strip() else "(empty)")
check("  ...and the refusal names the REMEDY (raise the margin)", "REMEDY" in out)
rc, out = run_cli([RESET, "120"], crontab="29,59 * * * * /bin/bash /home/jay/.taos-usage/watch.sh\n")
check("  ...and that remedy actually works", rc == 0)
check("  ...clearing the jitter with room", rc == 0 and
      int(field(out, "EFFECTIVE").split()[1].rstrip("s")) > M.SCHEDULER_EARLY_JITTER_SECONDS,
      field(out, "EFFECTIVE").strip() if rc == 0 else "")
# A tick whose arm lands on a jittered minute is skipped while its neighbours are not.
rc, out = run_cli([RESET], crontab="29,40 * * * * /bin/bash /home/jay/.taos-usage/watch.sh\n")
check("a jittered tick is SKIPPED while a safe neighbour still derives", rc == 0 and "SKIPPED" in out,
      field(out, "CRON").strip() if rc == 0 else out.strip().splitlines()[0][:50])
check("  ...and the surviving arm is NOT on a jittered minute",
      rc == 0 and stamp(out, "FIRES AT").astimezone().minute not in M.JITTERED_MINUTES)
for m in [0, 30]:
    check(f"minute :{m:02d} is declared jittered", m in M.JITTERED_MINUTES)

print("SURFACE: retry lead DEGENERATE values")
pf2 = datetime.datetime.fromisoformat("2026-08-16T08:07:00+00:00")
for lead in [0, -900]:
    tk, ar, fr = M.retry_after(pf2, mins, 60, lead=lead)
    check(f"lead={lead}: retry STILL lands after the primary (never before)", fr > pf2,
          f"primary={pf2:%H:%M} retry={fr:%H:%M}")
    check(f"lead={lead}: retry still lands on a real tick", tk.minute in mins)

print("RED DIRECTION: lead below the measured maximum admits the race it guards against")
measured_max = 49.729
pf = datetime.datetime.fromisoformat("2026-08-16T08:07:00+00:00")
primary_delete = pf + datetime.timedelta(seconds=measured_max)
for lead in [40, 49]:
    tk, ar, fr = M.retry_after(pf, mins, 60, lead=lead)
    gap = (fr - pf).total_seconds()
    check(f"lead={lead}: gap {gap:.0f}s >= lead {lead}s (contract holds)",
          gap >= lead,
          f"gap={gap:.0f}s")
    # The retry_after contract guarantees gap >= lead. With lead < measured_max,
    # the guaranteed minimum gap is less than the primary's observed delete time.
    # A retry at primary + lead fires BEFORE the delete at primary + 49.729s.
    retry_time = pf + datetime.timedelta(seconds=lead)
    check(f"lead={lead}: retry at {retry_time.isoformat()} fires BEFORE delete at {primary_delete.isoformat()}",
          retry_time < primary_delete,
          f"retry={retry_time.isoformat()} delete={primary_delete.isoformat()}")
check("RETRY_LEAD_SECONDS=60 is above the measured max (the race is bounded)",
      M.RETRY_LEAD_SECONDS >= measured_max)

print()
if FAILS:
    print(f"FAILED {len(FAILS)}:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL SURFACE TESTS PASS")
