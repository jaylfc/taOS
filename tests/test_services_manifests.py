"""Weight-license honesty guard for app-catalog/services manifests (#169).

MusicGen, MusicGPT, and FLUX.1-Fill run permissively-licensed (MIT) wrapper
code but pull pretrained weights that are non-commercial (Meta's CC-BY-NC 4.0
MusicGen weights; the FLUX.1-dev non-commercial license). This test guards
against a manifest's `license: MIT` line quietly implying the weights are
also permissive, by asserting the explicit `license_class` / `weights_license`
fields stay set on the services known to bundle non-commercial weights, and
are not accidentally added to (or dropped from) any other manifest.
"""
from pathlib import Path

import yaml

SERVICES_DIR = Path(__file__).parent.parent / "app-catalog" / "services"

# Explicit allowlist rather than inferred from the `license` string: most
# custom/community model licenses in this catalog (e.g. HunyuanVideo's
# Tencent Community License, Stability AI's Community License) permit
# commercial use below a revenue/usage threshold, which is a different legal
# category from "non-commercial" (CC-BY-NC-style, no commercial use at all).
NON_COMMERCIAL_WEIGHTS_SERVICES = {"musicgen", "musicgpt", "flux-fill"}


class TestServiceManifestsValid:
    def test_all_manifests_valid_yaml(self):
        manifests = list(SERVICES_DIR.glob("*/manifest.yaml"))
        assert len(manifests) >= 70
        for path in manifests:
            data = yaml.safe_load(path.read_text())
            assert "id" in data
            assert "license" in data


class TestNonCommercialWeightsLicensing:
    def test_known_non_commercial_services_carry_license_class(self):
        for app_id in NON_COMMERCIAL_WEIGHTS_SERVICES:
            path = SERVICES_DIR / app_id / "manifest.yaml"
            data = yaml.safe_load(path.read_text())
            assert data.get("license_class") == "non-commercial", (
                f"{app_id} bundles non-commercial weights but manifest "
                f"license_class is {data.get('license_class')!r}"
            )
            assert data.get("weights_license"), f"{app_id} missing weights_license"
            # The code license stays honest (MIT wrapper) -- only the weights
            # are restricted, so `license` must not be silently rewritten.
            assert data.get("license") == "MIT"

    def test_other_services_not_marked_non_commercial(self):
        for path in SERVICES_DIR.glob("*/manifest.yaml"):
            data = yaml.safe_load(path.read_text())
            if data["id"] in NON_COMMERCIAL_WEIGHTS_SERVICES:
                continue
            assert data.get("license_class", "") != "non-commercial", (
                f"{data['id']} unexpectedly marked non-commercial -- "
                f"add it to NON_COMMERCIAL_WEIGHTS_SERVICES if that's intentional"
            )
