from pathlib import Path

from clipapp_engine.media.hashing import HEAD_BYTES, file_hash


def test_hash_stable_and_content_sensitive(tmp_path: Path) -> None:
    f = tmp_path / "vídeo grande.bin"
    f.write_bytes(b"a" * 10)
    first = file_hash(f)
    assert first == file_hash(f)
    assert first.startswith("10-")
    f.write_bytes(b"b" * 10)
    assert file_hash(f) != first


def test_hash_ignores_bytes_after_head(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"x" * HEAD_BYTES + b"1")
    b.write_bytes(b"x" * HEAD_BYTES + b"2")
    assert file_hash(a).split("-")[2] == file_hash(b).split("-")[2]
