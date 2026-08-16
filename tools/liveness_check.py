import os, sys
def running(script):
    """True iff some process has `script` as an EXACT argv element (not a substring
    of a shell's command text). Compares whole argv entries, so a tool shell whose
    cmdline merely CONTAINS the path cannot match."""
    hits = []
    for pid in os.listdir('/proc'):
        if not pid.isdigit():
            continue
        try:
            with open(f'/proc/{pid}/cmdline', 'rb') as fh:
                argv = [a.decode('utf-8', 'replace') for a in fh.read().split(b'\0') if a]
        except (OSError, IOError):
            continue
        if not argv or len(argv) < 2:
            continue
        exe = os.path.basename(argv[0])
        if exe not in ('bash', 'sh', 'python3', 'python'):
            continue
        # argv[1:] compared as WHOLE elements; skip interpreter flags like -u
        if any(a == script for a in argv[1:]):
            hits.append(pid)
    return hits

T = '/home/jay/.taos-team/'
checks = [T+'a2a_watch.sh', T+'pr_watch.py', T+'lead_bus_watch.sh', T+'dispatch_loop.sh']
bad = 0
for c in checks:
    h = running(c)
    print(f"{'OK ' if h else 'DEAD'}  {os.path.basename(c):20s} pids={','.join(h) if h else '-'}")
    if not h:
        bad += 1
# NEGATIVE CONTROL in the same run: a script that cannot be running
ctrl = running(T+'DEFINITELY_NOT_RUNNING.sh')
print(f"CONTROL nonexistent watcher -> {'FALSE POSITIVE (check is broken)' if ctrl else 'correctly not found'}")
sys.exit(2 if ctrl else (1 if bad else 0))
