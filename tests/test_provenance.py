"""源码树指纹与语料清单：未 commit 工作区也要能对应到产物。"""

from app.config import PROJECT_ROOT
from eval.provenance import corpus_inventory, source_manifest


def test_corpus_inventory_hashes_files(tmp_path):
    (tmp_path / "a.jsonl").write_text('{"doc_id":"a"}\n', encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("hello", encoding="utf-8")
    inv = corpus_inventory(tmp_path)
    assert inv["exists"] is True
    assert inv["file_count"] == 2
    assert inv["total_bytes"] > 0
    assert all(entry["sha256"] for entry in inv["files"])
    assert {entry["path"] for entry in inv["files"]} == {"a.jsonl", "sub/b.txt"}


def test_source_manifest_records_uncommitted_tree():
    manifest = source_manifest(PROJECT_ROOT)
    assert manifest["schema_version"] == "1.0"
    assert len(manifest["tracked_diff_sha256"]) == 64
    assert len(manifest["source_tree_fingerprint"]) == 64
    again = source_manifest(PROJECT_ROOT)
    assert again["source_tree_fingerprint"] == manifest["source_tree_fingerprint"]