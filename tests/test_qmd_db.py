from tinyagentos.qmd_db import QmdDatabase


class TestQmdDatabase:
    def test_collections(self, qmd_db_path):
        db = QmdDatabase(qmd_db_path)
        collections = db.collections()
        assert len(collections) == 2
        names = {c["name"] for c in collections}
        assert "transcripts" in names
        assert "notes" in names

    def test_vector_count(self, qmd_db_path):
        db = QmdDatabase(qmd_db_path)
        assert db.vector_count() == 3

    def test_vector_count_by_collection(self, qmd_db_path):
        db = QmdDatabase(qmd_db_path)
        assert db.vector_count(collection="transcripts") == 2
        assert db.vector_count(collection="notes") == 1

    def test_browse_all(self, qmd_db_path):
        db = QmdDatabase(qmd_db_path)
        results = db.browse(limit=10, offset=0)
        assert len(results) == 3
        assert results[0]["hash"] == "ghi789"

    def test_browse_by_collection(self, qmd_db_path):
        db = QmdDatabase(qmd_db_path)
        results = db.browse(collection="transcripts", limit=10, offset=0)
        assert len(results) == 2
        assert all(r["collection"] == "transcripts" for r in results)

    def test_keyword_search(self, qmd_db_path):
        db = QmdDatabase(qmd_db_path)
        results = db.keyword_search("roadmap", limit=10)
        assert len(results) >= 1
        assert results[0]["hash"] == "abc123"

    def test_keyword_search_no_results(self, qmd_db_path):
        db = QmdDatabase(qmd_db_path)
        results = db.keyword_search("xyznonexistent", limit=10)
        assert results == []

    def test_delete_chunk(self, qmd_db_path):
        db = QmdDatabase(qmd_db_path)
        assert db.vector_count() == 3
        db.delete_chunk("abc123")
        assert db.vector_count() == 2
        results = db.keyword_search("roadmap", limit=10)
        assert len(results) == 0

    def test_last_embedded_at(self, qmd_db_path):
        db = QmdDatabase(qmd_db_path)
        ts = db.last_embedded_at()
        assert ts == "2026-04-03"
