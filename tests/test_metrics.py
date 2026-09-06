import time
import pytest
import pytest_asyncio
from tinyagentos.metrics import MetricsStore

@pytest_asyncio.fixture
async def metrics_store(tmp_path):
    store = MetricsStore(tmp_path / "metrics.db")
    await store.init()
    yield store
    await store.close()

@pytest.mark.asyncio
class TestMetricsStore:
    async def test_insert_and_query(self, metrics_store):
        now = int(time.time())
        await metrics_store.insert("test.metric", 42.0, now)
        results = await metrics_store.query("test.metric", start=now - 10, end=now + 10)
        assert len(results) == 1
        assert results[0]["value"] == 42.0
        assert results[0]["timestamp"] == now

    async def test_query_with_labels(self, metrics_store):
        now = int(time.time())
        await metrics_store.insert("backend.response_ms", 150.0, now, labels={"backend": "rkllama"})
        await metrics_store.insert("backend.response_ms", 200.0, now, labels={"backend": "ollama"})
        results = await metrics_store.query("backend.response_ms", start=now - 10, end=now + 10, labels={"backend": "rkllama"})
        assert len(results) == 1
        assert results[0]["value"] == 150.0

    async def test_query_empty_range(self, metrics_store):
        now = int(time.time())
        await metrics_store.insert("test.metric", 1.0, now)
        results = await metrics_store.query("test.metric", start=now + 100, end=now + 200)
        assert results == []

    async def test_retention_cleanup(self, metrics_store):
        old_ts = int(time.time()) - (31 * 86400)
        new_ts = int(time.time())
        await metrics_store.insert("test.metric", 1.0, old_ts)
        await metrics_store.insert("test.metric", 2.0, new_ts)
        deleted = await metrics_store.cleanup(retention_days=30)
        assert deleted == 1
        results = await metrics_store.query("test.metric", start=0, end=new_ts + 10)
        assert len(results) == 1
        assert results[0]["value"] == 2.0

    async def test_latest(self, metrics_store):
        now = int(time.time())
        await metrics_store.insert("test.metric", 1.0, now - 10)
        await metrics_store.insert("test.metric", 2.0, now)
        result = await metrics_store.latest("test.metric")
        assert result is not None
        assert result["value"] == 2.0
