#!/usr/bin/env python3
"""Check every /api/ path cited in a doc against the routes actually declared
in tinyagentos/routes/*.py.

Restoring deleted documentation reintroduces whatever drift accumulated while
it was gone. Kilo found one stale path by review; this finds the rest by
construction.
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
doc = Path(sys.argv[1]).read_text()

# --- declared routes -------------------------------------------------------
DECL = re.compile(r'@router\.(get|post|put|patch|delete)\(\s*[\'"]([^\'"]+)[\'"]')
declared = set()
for f in sorted((REPO / "tinyagentos" / "routes").glob("*.py")):
    for m in DECL.finditer(f.read_text()):
        declared.add(m.group(2))
# Some routers are mounted with a prefix; collect those too.
for f in sorted((REPO / "tinyagentos").rglob("*.py")):
    try:
        txt = f.read_text()
    except Exception:
        continue
    for m in re.finditer(r'include_router\([^)]*prefix\s*=\s*[\'"]([^\'"]+)[\'"]', txt):
        pass  # prefixes noted below if needed


def norm(p):
    """Collapse path params so {slug} and {id} compare equal."""
    p = p.rstrip("/")
    p = re.sub(r"\{[^}]*\}", "{}", p)
    return p


declared_norm = {norm(d) for d in declared}

# --- cited paths -----------------------------------------------------------
# Only take paths in code spans or after an HTTP verb, to avoid prose noise.
cited = set()
for m in re.finditer(r"`([A-Z]+ )?(/api/[^`\s]+)`", doc):
    cited.add(m.group(2))
for m in re.finditer(r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+(/api/[^\s`,)]+)", doc):
    cited.add(m.group(1))

missing = []
for c in sorted(cited):
    base = c.split("?")[0].rstrip(".,;:")
    n = norm(base)
    if n in declared_norm:
        continue
    # tolerate a documented parent of a real subtree (e.g. /api/desktop/*)
    if base.endswith("/*") and any(d.startswith(norm(base[:-2])) for d in declared_norm):
        continue
    if any(d == n or d.startswith(n + "/") for d in declared_norm):
        continue
    missing.append(c)

print(f"declared routes: {len(declared)}   cited in doc: {len(cited)}")
if missing:
    print(f"\nCITED BUT NOT DECLARED ({len(missing)}):")
    for m_ in missing:
        print(f"  {m_}")
    # Offer the closest declared match to make each one actionable.
    import difflib
    print("\nclosest declared match per miss:")
    for m_ in missing:
        base = norm(m_.split("?")[0].rstrip(".,;:"))
        near = difflib.get_close_matches(base, sorted(declared_norm), n=2, cutoff=0.6)
        print(f"  {m_}  ->  {near}")
    sys.exit(1)
print("all cited /api/ paths are declared")
