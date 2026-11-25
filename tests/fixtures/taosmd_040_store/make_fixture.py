"""Regenerate this directory with a taosmd 0.4.0 interpreter (not 0.5.x).

    python make_fixture.py <out_dir>

The committed files were written by taosmd 0.4.0 and are read by
tests/test_taosmd_040_store_upgrade.py. Do not regenerate them with a newer
taosmd: the point of the fixture is that a newer build did NOT write it.
"""
import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path

import taosmd
from taosmd import archive, knowledge_graph, vector_memory

assert taosmd.__version__ == "0.4.0", taosmd.__version__
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)


async def main() -> None:
    kg = knowledge_graph.TemporalKnowledgeGraph(out / "knowledge-graph.db")
    await kg.init()
    await kg.add_triple("Jay", "likes", "tea")
    await kg.close()
    vm = vector_memory.VectorMemory(db_path=out / "vector-memory.db")
    await vm.init()
    await vm.close()
    a = archive.ArchiveStore(archive_dir=out / "archive", index_path=out / "archive-index.db")
    await a.init()
    await a.record("conversation", {"text": "hello from 0.4.0"}, agent_name="tester", summary="hello")
    await a.close()


asyncio.run(main())
# One legacy JSON-encoded dense vector, as a 0.4.0 store holds them.
conn = sqlite3.connect(out / "vector-memory.db")
conn.execute(
    "insert into vector_memory(text, embedding, metadata_json, created_at) values (?,?,?,?)",
    ("legacy fact", json.dumps([0.1, 0.2, 0.3, 0.4]), "{}", time.time()),
)
conn.commit()
conn.close()
