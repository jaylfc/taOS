"""Engine for catalog licence policy: which licences need a person's acceptance.

Pure functions, no I/O. The Store install route, the manifest lint and the
tests all use this one module, so "restrictive" means the same thing
everywhere.

A manifest can carry several licence fields (the code ``license``, the
``weights_license`` of the model it downloads, a ``model_license``) and an
explicit ``license_class``. The gate keys off ANY of them: if any field is
restrictive, the person has to accept before the install runs.

Three outcomes for a licence string:

* permissive: on the allowlist, no acceptance needed.
* restrictive: known, with a class (non-commercial, research-only,
  revenue-capped, use-restricted, proprietary, territory-limited).
* unknown: not on either list. The catalog lint fails on these; at install
  time they are treated as restrictive (class ``unrecognised``) so nothing
  slips through because nobody classified it.

Acceptance can only come from a person's own session (``ACCEPT_VIAS``). Agent
tokens, service tokens and registry JWTs are refused.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# The only credential kind that may accept a licence: a signed-in person.
ACCEPT_VIAS = frozenset({"session"})


def via_may_accept(via: object) -> bool:
    """True only for a person's own session (allow-list, not deny-list)."""
    return isinstance(via, str) and via in ACCEPT_VIAS


EU_MEMBERS = (
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
    "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
    "SI", "ES", "SE",
)

RESTRICTIVE_CLASSES = frozenset({
    "non-commercial", "research-only", "revenue-capped", "use-restricted",
    "proprietary", "territory-limited", "unrecognised",
})


def _key(text: str) -> str:
    t = re.sub(r"\([^)]*\)", " ", str(text).lower())
    t = re.sub(r"[^a-z0-9.+]+", " ", t)
    return " ".join(t.split())


@dataclass(frozen=True)
class KnownLicence:
    name: str
    cls: str
    url: str = ""
    excluded_territories: tuple[str, ...] = ()


_PERMISSIVE_NAMES = (
    "MIT", "MIT-0", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC",
    "MPL-2.0", "AGPL-3.0", "AGPL-3.0-or-later", "GPL-3.0", "GPL-3.0-or-later",
    "LGPL-2.1", "LGPL-3.0", "CC-BY-4.0", "CC0-1.0", "Unlicense", "PSF-2.0",
    "0BSD", "Zlib",
)
PERMISSIVE = frozenset(_key(n) for n in _PERMISSIVE_NAMES)

_NC_URL = "https://creativecommons.org/licenses/by-nc/4.0/legalcode"
_KNOWN = (
    KnownLicence("CC-BY-NC-4.0", "non-commercial", _NC_URL),
    KnownLicence("CC-BY-NC 4.0", "non-commercial", _NC_URL),
    KnownLicence("CC BY-NC-SA 4.0", "non-commercial",
                 "https://creativecommons.org/licenses/by-nc-sa/4.0/legalcode"),
    KnownLicence("flux-1-dev-non-commercial-license", "non-commercial"),
    KnownLicence("sai-nc-community", "non-commercial"),
    KnownLicence("stable-cascade-nc-community", "non-commercial"),
    KnownLicence("bria-rmbg-1.4", "non-commercial"),
    KnownLicence("S-Lab License 1.0", "non-commercial"),
    KnownLicence("Qwen Research License", "research-only"),
    KnownLicence("Tongyi Qianwen License", "revenue-capped"),
    KnownLicence("Qwen License", "revenue-capped"),
    KnownLicence("Llama 3.1 Community License", "revenue-capped",
                 "https://www.llama.com/llama3_1/license/"),
    KnownLicence("Llama 3.3 Community License", "revenue-capped",
                 "https://www.llama.com/llama3_3/license/"),
    KnownLicence("llama3.2", "revenue-capped", "https://www.llama.com/llama3_2/license/"),
    KnownLicence("Stability AI Community License", "revenue-capped",
                 "https://stability.ai/community-license-agreement"),
    KnownLicence("Gemma Terms of Use", "use-restricted", "https://ai.google.dev/gemma/terms"),
    KnownLicence("Gemma", "use-restricted", "https://ai.google.dev/gemma/terms"),
    KnownLicence("DeepSeek License", "use-restricted"),
    KnownLicence("OpenRAIL", "use-restricted"),
    KnownLicence("OpenRAIL-M", "use-restricted"),
    KnownLicence("OpenRAIL++", "use-restricted"),
    KnownLicence("CreativeML OpenRAIL-M", "use-restricted"),
    KnownLicence("NVIDIA Open Model License", "use-restricted"),
    KnownLicence("Playground v2.5 Community License", "use-restricted"),
    KnownLicence("Sustainable Use License", "use-restricted",
                 "https://docs.n8n.io/sustainable-use-license/"),
    KnownLicence("Apache-2.0 / MiniCPM Model License", "use-restricted"),
    KnownLicence("Proprietary", "proprietary"),
    KnownLicence("Tencent HunyuanVideo Community License", "territory-limited",
                 "https://github.com/Tencent/HunyuanVideo/blob/main/LICENSE",
                 excluded_territories=EU_MEMBERS + ("GB", "KR")),
)
KNOWN = {_key(k.name): k for k in _KNOWN}


def classify(text: str) -> tuple[str, KnownLicence | None]:
    """Return ("permissive"|"restrictive"|"unknown", known-entry-or-None)."""
    key = _key(text)
    if not key:
        return "unknown", None
    if key in PERMISSIVE:
        return "permissive", None
    if key in KNOWN:
        return "restrictive", KNOWN[key]
    # "A OR B": acceptable if either side is; otherwise the first restrictive side.
    parts = re.split(r"\s+OR\s+", str(text).strip())
    if len(parts) > 1:
        results = [classify(p) for p in parts]
        for verdict, entry in results:
            if verdict == "permissive":
                return "permissive", None
        for verdict, entry in results:
            if verdict == "restrictive":
                return "restrictive", entry
    return "unknown", None


LICENCE_FIELDS = ("license", "weights_license", "model_license")


@dataclass
class Restriction:
    """What the gate found on a manifest. ``restrictive`` False means no gate."""
    restrictive: bool = False
    cls: str = ""
    # The licence the person is asked to accept (name as written, version, link).
    name: str = ""
    version: str = ""
    url: str = ""
    excluded_territories: tuple[str, ...] = ()
    fields: list[str] = field(default_factory=list)

    @property
    def territory_limited(self) -> bool:
        return bool(self.excluded_territories)

    @property
    def acceptance_key(self) -> str:
        """Key an acceptance is stored under: name plus version, so a new
        version of the same licence needs a fresh acceptance."""
        return f"{self.name} {self.version}".strip() if self.version else self.name


def _text(manifest, attr: str) -> str:
    value = getattr(manifest, attr, "")
    return value.strip() if isinstance(value, str) else ""


def _strs(manifest, attr: str) -> tuple[str, ...]:
    value = getattr(manifest, attr, None)
    if isinstance(value, (list, tuple)):
        return tuple(str(v).upper() for v in value if isinstance(v, str))
    return ()


def evaluate(manifest, manifest_id: str = "") -> Restriction:
    """Inspect every licence field of ``manifest`` and report restrictions.

    Restrictive if ``license_class`` is anything other than empty/permissive,
    or ANY of the licence fields is restrictive or unrecognised.
    """
    found: list[tuple[str, str, KnownLicence | None]] = []  # (field, cls, entry)
    explicit = _text(manifest, "license_class").lower()
    if explicit and explicit != "permissive":
        found.append(("license_class", explicit if explicit in RESTRICTIVE_CLASSES else "unrecognised", None))
    for attr in LICENCE_FIELDS:
        text = _text(manifest, attr)
        if not text:
            continue
        verdict, entry = classify(text)
        if verdict == "restrictive" and entry is not None:
            found.append((attr, entry.cls, entry))
        elif verdict == "unknown":
            found.append((attr, "unrecognised", None))
    if not found:
        return Restriction()

    # The licence to show: prefer the weights licence, then the model licence,
    # then the code licence, then whatever the explicit class refers to.
    order = {"weights_license": 0, "model_license": 1, "license": 2, "license_class": 3}
    found.sort(key=lambda f: order[f[0]])
    shown_field = next((f for f in found if f[0] != "license_class"), found[0])
    name = _text(manifest, shown_field[0]) if shown_field[0] != "license_class" else ""
    if not name:
        name = _text(manifest, "weights_license") or _text(manifest, "license") or manifest_id
    entry = next((f[2] for f in found if f[2] is not None), None)
    excluded = _strs(manifest, "license_excluded_territories")
    if not excluded:
        excluded = next((f[2].excluded_territories for f in found if f[2] and f[2].excluded_territories), ())
    cls = "territory-limited" if excluded else found[0][1]
    return Restriction(
        restrictive=True,
        cls=cls,
        name=name,
        version=_text(manifest, "license_version"),
        url=_text(manifest, "license_url") or (entry.url if entry else ""),
        excluded_territories=tuple(excluded),
        fields=[f[0] for f in found],
    )


_CLASS_TERMS = {
    "non-commercial": "for non-commercial use only",
    "research-only": "for research use only",
    "revenue-capped": "with commercial use allowed only below the limits set in that licence",
    "use-restricted": "with use restrictions set by that licence",
    "proprietary": "under proprietary terms",
    "territory-limited": "with use limited to certain countries",
    "unrecognised": "under terms taOS does not recognise, so read them before you agree",
}


def acceptance_text(app_name: str, r: Restriction) -> str:
    """The text the person agrees to. Fully built, no placeholders."""
    ver = f" version {r.version}" if r.version else ""
    terms = _CLASS_TERMS.get(r.cls, _CLASS_TERMS["unrecognised"])
    where = f" The full text is at {r.url}." if r.url else ""
    return (
        f"{app_name} uses {r.name}{ver}, {terms}.{where} "
        f"Continuing means you agree to that licence for the software and model "
        f"weights {app_name} uses. Only you can accept; an agent cannot accept for you."
    )


def territory_check(r: Restriction, country: str | None) -> str:
    """"ok", "needs_country" or "excluded".

    Only a territory-limited licence cares about the country. Unset country
    fails closed there and nowhere else.
    """
    if not r.territory_limited:
        return "ok"
    code = (country or "").strip().upper()
    if not code:
        return "needs_country"
    return "excluded" if code in r.excluded_territories else "ok"


def valid_country(code: object) -> str | None:
    """Normalised ISO 3166-1 alpha-2 code, or None if it is not one."""
    if isinstance(code, str) and re.fullmatch(r"[A-Za-z]{2}", code.strip()):
        return code.strip().upper()
    return None
