from __future__ import annotations
import sqlite3
from pathlib import Path

from tinyagentos.db_migrations import apply_wal_pragmas


class QmdDatabase:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(f"QMD database not found: {db_path}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        apply_wal_pragmas(conn)
        return conn

    def collections(self) -> list[dict]:
        conn = self._connect()
        try:
            rows = conn.execute("""
                SELECT sc.name, sc.path, sc.pattern,
                       COUNT(DISTINCT d.id) as doc_count,
                       COUNT(cv.hash) as vector_count
                FROM store_collections sc
                LEFT JOIN documents d ON d.collection = sc.name AND d.active = 1
                LEFT JOIN content_vectors cv ON cv.hash = d.hash
                GROUP BY sc.name
                ORDER BY sc.name
            """).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def vector_count(self, collection: str | None = None) -> int:
        conn = self._connect()
        try:
            if collection:
                row = conn.execute("""
                    SELECT COUNT(*) FROM content_vectors cv
                    JOIN documents d ON d.hash = cv.hash AND d.active = 1
                    WHERE d.collection = ?
                """, (collection,)).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM content_vectors").fetchone()
            return row[0]
        finally:
            conn.close()

    def browse(self, collection: str | None = None, limit: int = 20, offset: int = 0) -> list[dict]:
        conn = self._connect()
        try:
            sql = """
                SELECT c.hash, c.doc, c.created_at, d.collection, d.path, d.title
                FROM content c
                JOIN documents d ON d.hash = c.hash AND d.active = 1
            """
            params: list = []
            if collection:
                sql += " WHERE d.collection = ?"
                params.append(collection)
            sql += " ORDER BY c.created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def keyword_search(self, query: str, collection: str | None = None, limit: int = 20) -> list[dict]:
        conn = self._connect()
        try:
            sql = """
                SELECT c.hash, c.doc, c.created_at, d.collection, d.path, d.title,
                       rank AS score
                FROM documents_fts fts
                JOIN documents d ON d.id = fts.rowid AND d.active = 1
                JOIN content c ON c.hash = d.hash
                WHERE documents_fts MATCH ?
            """
            params: list = [query]
            if collection:
                sql += " AND d.collection = ?"
                params.append(collection)
            sql += " ORDER BY rank LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def delete_chunk(self, content_hash: str) -> None:
        conn = self._connect()
        try:
            doc_ids = conn.execute("SELECT id FROM documents WHERE hash = ?", (content_hash,)).fetchall()
            for row in doc_ids:
                conn.execute("DELETE FROM documents_fts WHERE rowid = ?", (row[0],))
            conn.execute("DELETE FROM content_vectors WHERE hash = ?", (content_hash,))
            conn.execute("DELETE FROM documents WHERE hash = ?", (content_hash,))
            conn.execute("DELETE FROM content WHERE hash = ?", (content_hash,))
            conn.commit()
        finally:
            conn.close()

    def last_embedded_at(self) -> str | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT embedded_at FROM content_vectors ORDER BY embedded_at DESC LIMIT 1").fetchone()
            return row[0] if row else None
        finally:
            conn.close()
