"""Index metadata refresh with fake clients; no embeddings or network calls."""
from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_vector_db as builder
from test_live_core import fake_google_sdk


class IndexRefreshTests(unittest.TestCase):
    def test_unchanged_documents_refresh_parent_metadata_without_embeddings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chunk = builder.ChunkRecord("fixture-0", "unchanged document", {"parent_text": "new local window"})
            collection = SimpleNamespace(
                metadata={"embedding_model": builder.EMBEDDING_MODEL, "chunking_version": "old"},
                get=Mock(return_value={"ids": [chunk.chunk_id], "documents": [chunk.document]}),
                modify=Mock(), update=Mock(), count=Mock(return_value=1),
                add=Mock(), delete=Mock(),
            )
            chroma = SimpleNamespace(PersistentClient=lambda **kwargs: SimpleNamespace(
                get_or_create_collection=lambda **kwargs: collection))
            client = SimpleNamespace(models=SimpleNamespace())
            with patch.dict(os.environ, {"SUGANG_OFFLINE": "0", "GOOGLE_API_KEY": "fake-test-key"}), \
                 patch.dict(sys.modules, {"chromadb": chroma}), fake_google_sdk(client), \
                 patch.object(builder, "load_records", return_value=[object()]), \
                 patch.object(builder, "make_chunks", return_value=[chunk]), \
                 patch.object(builder, "resolve_chroma_dir", return_value=root / "chroma"), \
                 patch.object(builder, "dataset_fingerprint", return_value="fixture-sha"), \
                 patch.object(builder, "embed_batch_with_retry", side_effect=AssertionError("No embeddings needed")), \
                 patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")), \
                 redirect_stdout(io.StringIO()):
                builder.build(root, batch_size=8, sleep_seconds=0, max_retries=1, retry_seconds=0, reset=False)
            collection.update.assert_called_once_with(ids=[chunk.chunk_id], metadatas=[chunk.metadata])
            collection.add.assert_not_called()
            collection.delete.assert_not_called()
            final = collection.modify.call_args.kwargs["metadata"]
            self.assertEqual(final["chunking_version"], builder.CHUNKING_VERSION)
            self.assertEqual(final["dataset_sha256"], "fixture-sha")


if __name__ == "__main__":
    unittest.main()
