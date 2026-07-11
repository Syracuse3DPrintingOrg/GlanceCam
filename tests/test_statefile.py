from app.statefile import StateFile


def test_write_then_read_roundtrip(tmp_path):
    sf = StateFile(tmp_path / "state.json", default={"items": []})
    sf.write({"items": [1, 2, 3]})
    assert sf.read() == {"items": [1, 2, 3]}


def test_read_default_when_missing(tmp_path):
    sf = StateFile(tmp_path / "missing.json", default={"k": "v"})
    assert sf.read() == {"k": "v"}


def test_read_refreshes_when_mtime_changes(tmp_path):
    import json
    import os

    path = tmp_path / "s.json"
    sf = StateFile(path, default={})
    sf.write({"n": 1})
    assert sf.read() == {"n": 1}
    # Rewrite the file out of band and force a later mtime, so the read must
    # re-parse rather than serve the cache.
    path.write_text(json.dumps({"n": 2}))
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert sf.read() == {"n": 2}


def test_corrupt_file_falls_back_to_default(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json")
    sf = StateFile(path, default={"ok": True})
    assert sf.read() == {"ok": True}


def test_atomic_write_leaves_no_temp(tmp_path):
    path = tmp_path / "a.json"
    sf = StateFile(path, default={})
    sf.write({"x": 1})
    assert not (tmp_path / "a.json.tmp").exists()
    assert path.exists()


def test_unwritable_dir_degrades_in_memory(tmp_path):
    # A path under a file (not a dir) cannot be created; write must not raise and
    # the value should still be readable in-process.
    not_a_dir = tmp_path / "afile"
    not_a_dir.write_text("x")
    sf = StateFile(not_a_dir / "nested" / "s.json", default={})
    sf.write({"kept": 1})
    assert sf.read() == {"kept": 1}
