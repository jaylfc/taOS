"""Unit tests for tinyagentos.cluster.worker_protocol WorkerInfo and GpuLease."""
from __future__ import annotations

from dataclasses import asdict

import pytest

from tinyagentos.cluster.worker_protocol import GpuLease, WorkerInfo


class TestWorkerInfoDefaults:
    """WorkerInfo should expose sensible defaults for every optional field."""

    def test_name_and_url_required(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000")
        assert w.name == "w1"
        assert w.url == "http://localhost:9000"

    def test_hardware_defaults_to_empty_dict(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000")
        assert w.hardware == {}

    def test_backends_defaults_to_empty_list(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000")
        assert w.backends == []

    def test_models_defaults_to_empty_list(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000")
        assert w.models == []

    def test_available_models_defaults_to_empty_list(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000")
        assert w.available_models == []

    def test_capabilities_defaults_to_empty_list(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000")
        assert w.capabilities == []

    def test_status_defaults_to_online(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000")
        assert w.status == "online"

    def test_last_heartbeat_defaults_to_zero(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000")
        assert w.last_heartbeat == 0

    def test_registered_at_defaults_to_zero(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000")
        assert w.registered_at == 0

    def test_load_defaults_to_zero(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000")
        assert w.load == 0.0

    def test_platform_defaults_to_empty_string(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000")
        assert w.platform == ""

    def test_tier_id_defaults_to_empty_string(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000")
        assert w.tier_id == ""

    def test_potential_capabilities_defaults_to_empty_list(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000")
        assert w.potential_capabilities == []

    def test_kv_cache_quant_support_defaults_to_fp16(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000")
        assert w.kv_cache_quant_support == ["fp16"]

    def test_kv_cache_quant_k_support_defaults_to_fp16(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000")
        assert w.kv_cache_quant_k_support == ["fp16"]

    def test_kv_cache_quant_v_support_defaults_to_fp16(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000")
        assert w.kv_cache_quant_v_support == ["fp16"]

    def test_kv_cache_quant_boundary_layer_protect_defaults_to_false(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000")
        assert w.kv_cache_quant_boundary_layer_protect is False

    def test_worker_url_defaults_to_none(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000")
        assert w.worker_url is None

    def test_signing_key_defaults_to_empty_bytes(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000")
        assert isinstance(w.signing_key, bytes)
        assert w.signing_key == b""

    def test_tls_cert_provider_defaults_to_none(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000")
        assert w.tls_cert_provider is None

    def test_host_lan_ip_defaults_to_none(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000")
        assert w.host_lan_ip is None

    def test_storage_cap_bytes_defaults_to_zero(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000")
        assert w.storage_cap_bytes == 0

    def test_storage_used_bytes_defaults_to_zero(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000")
        assert w.storage_used_bytes == 0

    def test_bytes_deduped_total_defaults_to_zero(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000")
        assert w.bytes_deduped_total == 0

    def test_worker_lxc_image_version_defaults_to_none(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000")
        assert w.worker_lxc_image_version is None

    def test_degraded_defaults_to_false(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000")
        assert w.degraded is False

    def test_degraded_reason_defaults_to_none(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000")
        assert w.degraded_reason is None

    def test_free_vram_mb_defaults_to_none(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000")
        assert w.free_vram_mb is None

    def test_used_vram_mb_defaults_to_none(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000")
        assert w.used_vram_mb is None


class TestWorkerInfoCustomValues:
    """WorkerInfo should store and expose any explicitly supplied value."""

    def test_can_set_all_string_and_list_fields(self):
        w = WorkerInfo(
            name="hogne",
            url="http://192.168.1.10:9000",
            hardware={"cpu": "8", "gpu": "A100"},
            backends=[{"name": "ollama:11434", "url": "http://localhost:11434"}],
            models=["llama3", "mistral"],
            available_models=[{"name": "llama3", "size": "7B"}],
            capabilities=["chat", "embed"],
            status="busy",
            last_heartbeat=1_700_000_000.0,
            registered_at=1_600_000_000.0,
            load=0.75,
            platform="linux",
            tier_id="x86-cuda-24gb",
            potential_capabilities=["chat", "rerank"],
            kv_cache_quant_support=["q4_0", "q8_0"],
            kv_cache_quant_k_support=["q4_0", "turbo2"],
            kv_cache_quant_v_support=["q8_0", "f16"],
            kv_cache_quant_boundary_layer_protect=True,
            worker_url="http://10.0.0.1:6969",
            signing_key=b"\xaa" * 32,
            tls_cert_provider="letsencrypt",
            host_lan_ip="10.0.0.1",
            storage_cap_bytes=1_000_000_000_000,
            storage_used_bytes=500_000_000_000,
            bytes_deduped_total=100_000_000,
            worker_lxc_image_version="ubuntu/24.04/amd64",
            degraded=True,
            degraded_reason="gpu overtemp",
            free_vram_mb=4096,
            used_vram_mb=16384,
        )
        assert w.name == "hogne"
        assert w.url == "http://192.168.1.10:9000"
        assert w.hardware == {"cpu": "8", "gpu": "A100"}
        assert w.backends == [{"name": "ollama:11434", "url": "http://localhost:11434"}]
        assert w.models == ["llama3", "mistral"]
        assert w.available_models == [{"name": "llama3", "size": "7B"}]
        assert w.capabilities == ["chat", "embed"]
        assert w.status == "busy"
        assert w.last_heartbeat == 1_700_000_000.0
        assert w.registered_at == 1_600_000_000.0
        assert w.load == 0.75
        assert w.platform == "linux"
        assert w.tier_id == "x86-cuda-24gb"
        assert w.potential_capabilities == ["chat", "rerank"]
        assert w.kv_cache_quant_support == ["q4_0", "q8_0"]
        assert w.kv_cache_quant_k_support == ["q4_0", "turbo2"]
        assert w.kv_cache_quant_v_support == ["q8_0", "f16"]
        assert w.kv_cache_quant_boundary_layer_protect is True
        assert w.worker_url == "http://10.0.0.1:6969"
        assert w.signing_key == b"\xaa" * 32
        assert w.tls_cert_provider == "letsencrypt"
        assert w.host_lan_ip == "10.0.0.1"
        assert w.storage_cap_bytes == 1_000_000_000_000
        assert w.storage_used_bytes == 500_000_000_000
        assert w.bytes_deduped_total == 100_000_000
        assert w.worker_lxc_image_version == "ubuntu/24.04/amd64"
        assert w.degraded is True
        assert w.degraded_reason == "gpu overtemp"
        assert w.free_vram_mb == 4096
        assert w.used_vram_mb == 16384

    def test_status_can_be_offline(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000", status="offline")
        assert w.status == "offline"

    def test_free_vram_mb_can_be_zero(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000", free_vram_mb=0)
        assert w.free_vram_mb == 0

    def test_used_vram_mb_can_be_zero(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000", used_vram_mb=0)
        assert w.used_vram_mb == 0


class TestWorkerInfoMutableDefaults:
    """Each WorkerInfo instance must get its own list copies, not shared refs."""

    def test_default_lists_are_independent(self):
        w1 = WorkerInfo(name="w1", url="http://localhost:9000")
        w2 = WorkerInfo(name="w2", url="http://localhost:9001")
        w1.capabilities.append("chat")
        assert w2.capabilities == []

    def test_default_kv_support_lists_are_independent(self):
        w1 = WorkerInfo(name="w1", url="http://localhost:9000")
        w2 = WorkerInfo(name="w2", url="http://localhost:9001")
        w1.kv_cache_quant_support.append("q4_0")
        assert w2.kv_cache_quant_support == ["fp16"]

    def test_default_kv_k_support_lists_are_independent(self):
        w1 = WorkerInfo(name="w1", url="http://localhost:9000")
        w2 = WorkerInfo(name="w2", url="http://localhost:9001")
        w1.kv_cache_quant_k_support.append("turbo2")
        assert w2.kv_cache_quant_k_support == ["fp16"]

    def test_default_kv_v_support_lists_are_independent(self):
        w1 = WorkerInfo(name="w1", url="http://localhost:9000")
        w2 = WorkerInfo(name="w2", url="http://localhost:9001")
        w1.kv_cache_quant_v_support.append("q8_0")
        assert w2.kv_cache_quant_v_support == ["fp16"]

    def test_hardware_dict_is_independent(self):
        w1 = WorkerInfo(name="w1", url="http://localhost:9000")
        w2 = WorkerInfo(name="w2", url="http://localhost:9001")
        w1.hardware["gpu"] = "A100"
        assert "gpu" not in w2.hardware


class TestWorkerInfoAsdict:
    """asdict should faithfully round-trip every field."""

    def test_asdict_contains_required_fields(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000")
        d = asdict(w)
        assert d["name"] == "w1"
        assert d["url"] == "http://localhost:9000"

    def test_asdict_contains_defaults(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000")
        d = asdict(w)
        assert d["status"] == "online"
        assert d["load"] == 0.0
        assert d["last_heartbeat"] == 0
        assert d["kv_cache_quant_support"] == ["fp16"]

    def test_asdict_contains_custom_values(self):
        w = WorkerInfo(
            name="w1",
            url="http://localhost:9000",
            status="busy",
            load=0.5,
            signing_key=b"\x01" * 16,
        )
        d = asdict(w)
        assert d["status"] == "busy"
        assert d["load"] == 0.5
        assert d["signing_key"] == b"\x01" * 16

    def test_asdict_includes_all_kv_fields(self):
        w = WorkerInfo(
            name="w1",
            url="http://localhost:9000",
            kv_cache_quant_support=["q4_0"],
            kv_cache_quant_k_support=["q4_0", "turbo2"],
            kv_cache_quant_v_support=["q8_0"],
            kv_cache_quant_boundary_layer_protect=True,
        )
        d = asdict(w)
        assert d["kv_cache_quant_support"] == ["q4_0"]
        assert d["kv_cache_quant_k_support"] == ["q4_0", "turbo2"]
        assert d["kv_cache_quant_v_support"] == ["q8_0"]
        assert d["kv_cache_quant_boundary_layer_protect"] is True


class TestWorkerInfoEdgeCases:
    """WorkerInfo should behave correctly at boundaries and with empty/None inputs."""

    def test_empty_name_is_accepted(self):
        w = WorkerInfo(name="", url="http://localhost:9000")
        assert w.name == ""

    def test_empty_url_is_accepted(self):
        w = WorkerInfo(name="w1", url="")
        assert w.url == ""

    def test_load_boundary_zero(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000", load=0.0)
        assert w.load == 0.0

    def test_load_boundary_one(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000", load=1.0)
        assert w.load == 1.0

    def test_storage_cap_bytes_zero(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000", storage_cap_bytes=0)
        assert w.storage_cap_bytes == 0

    def test_storage_used_bytes_zero(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000", storage_used_bytes=0)
        assert w.storage_used_bytes == 0

    def test_free_vram_mb_zero_is_not_none(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000", free_vram_mb=0)
        assert w.free_vram_mb == 0
        assert w.free_vram_mb is not None

    def test_used_vram_mb_zero_is_not_none(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000", used_vram_mb=0)
        assert w.used_vram_mb == 0
        assert w.used_vram_mb is not None

    def test_none_host_lan_ip(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000", host_lan_ip=None)
        assert w.host_lan_ip is None

    def test_empty_kv_support_lists(self):
        w = WorkerInfo(
            name="w1",
            url="http://localhost:9000",
            kv_cache_quant_support=[],
            kv_cache_quant_k_support=[],
            kv_cache_quant_v_support=[],
        )
        assert w.kv_cache_quant_support == []
        assert w.kv_cache_quant_k_support == []
        assert w.kv_cache_quant_v_support == []

    def test_empty_capabilities(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000", capabilities=[])
        assert w.capabilities == []

    def test_empty_models(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000", models=[])
        assert w.models == []

    def test_degraded_without_reason(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000", degraded=True, degraded_reason=None)
        assert w.degraded is True
        assert w.degraded_reason is None

    def test_zero_bytes_deduped_total(self):
        w = WorkerInfo(name="w1", url="http://localhost:9000", bytes_deduped_total=0)
        assert w.bytes_deduped_total == 0


class TestGpuLeaseDefaults:
    """GpuLease should expose sensible defaults for optional fields."""

    def test_lease_id_and_resource_id_required(self):
        lease = GpuLease(lease_id="l_aabbccdd", resource_id="hognehermes:gpu-cuda-0")
        assert lease.lease_id == "l_aabbccdd"
        assert lease.resource_id == "hognehermes:gpu-cuda-0"

    def test_caller_defaults_to_empty_string(self):
        lease = GpuLease(lease_id="l_aabbccdd", resource_id="hognehermes:gpu-cuda-0")
        assert lease.caller == ""

    def test_expires_at_defaults_to_zero(self):
        lease = GpuLease(lease_id="l_aabbccdd", resource_id="hognehermes:gpu-cuda-0")
        assert lease.expires_at == 0.0

    def test_required_vram_mb_defaults_to_zero(self):
        lease = GpuLease(lease_id="l_aabbccdd", resource_id="hognehermes:gpu-cuda-0")
        assert lease.required_vram_mb == 0


class TestGpuLeaseCustomValues:
    """GpuLease should store any explicitly supplied value."""

    def test_full_lease(self):
        lease = GpuLease(
            lease_id="l_12345678",
            resource_id="worker-a:gpu-cuda-0",
            caller="skald-dispatcher",
            expires_at=1_700_000_000.0,
            required_vram_mb=12288,
        )
        assert lease.lease_id == "l_12345678"
        assert lease.resource_id == "worker-a:gpu-cuda-0"
        assert lease.caller == "skald-dispatcher"
        assert lease.expires_at == 1_700_000_000.0
        assert lease.required_vram_mb == 12288

    def test_caller_can_be_set_to_non_empty(self):
        lease = GpuLease(
            lease_id="l_abcdef01",
            resource_id="w:b",
            caller="a2a-agent:extract",
        )
        assert lease.caller == "a2a-agent:extract"

    def test_expires_at_boundary_zero(self):
        lease = GpuLease(lease_id="l_aabbccdd", resource_id="w:b", expires_at=0.0)
        assert lease.expires_at == 0.0

    def test_required_vram_mb_zero(self):
        lease = GpuLease(lease_id="l_aabbccdd", resource_id="w:b", required_vram_mb=0)
        assert lease.required_vram_mb == 0


class TestGpuLeaseAsdict:
    """asdict should faithfully round-trip every GpuLease field."""

    def test_asdict_defaults(self):
        lease = GpuLease(lease_id="l_aabbccdd", resource_id="w:b")
        d = asdict(lease)
        assert d["lease_id"] == "l_aabbccdd"
        assert d["resource_id"] == "w:b"
        assert d["caller"] == ""
        assert d["expires_at"] == 0.0
        assert d["required_vram_mb"] == 0

    def test_asdict_custom_values(self):
        lease = GpuLease(
            lease_id="l_12345678",
            resource_id="worker-a:gpu-cuda-0",
            caller="skald-dispatcher",
            expires_at=1_700_000_000.0,
            required_vram_mb=12288,
        )
        d = asdict(lease)
        assert d["caller"] == "skald-dispatcher"
        assert d["expires_at"] == 1_700_000_000.0
        assert d["required_vram_mb"] == 12288
