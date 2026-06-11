import urllib.error
import paperconan.fetch._download as dl


class _Resp:
    def __init__(self, body=b"col1,col2\n1,2\n", ctype="text/csv", clen=None):
        self._chunks = [body[i:i+4] for i in range(0, len(body), 4)] or [b""]
        self._ctype, self._clen, self._n = ctype, clen, len(body)
    def info(self):
        d = {"Content-Type": self._ctype}
        if self._clen is not None: d["Content-Length"] = str(self._clen)
        class H:
            def __init__(s, m): s.m = m
            def get(s, k, default=None): return s.m.get(k, default)
        return H(d)
    def read(self, n=-1):
        if not self._chunks: return b""
        return self._chunks.pop(0)
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_download_retries_then_succeeds(tmp_path, monkeypatch):
    calls = {"n": 0}
    def flaky_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.HTTPError(req.full_url, 500, "err", {}, None)
        return _Resp()
    monkeypatch.setattr(dl.urllib.request, "urlopen", flaky_urlopen)
    monkeypatch.setattr(dl.time, "sleep", lambda *_: None)  # no real backoff wait
    dest = tmp_path / "t.csv"
    res = dl.download_file("https://x/t.csv", str(dest), retries=3, backoff=0.0)
    assert res["ok"] is True
    assert calls["n"] == 3
    assert dest.read_bytes() == b"col1,col2\n1,2\n"   # streamed to disk correctly


def test_download_gives_up_after_retries(tmp_path, monkeypatch):
    def always_500(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 500, "err", {}, None)
    monkeypatch.setattr(dl.urllib.request, "urlopen", always_500)
    monkeypatch.setattr(dl.time, "sleep", lambda *_: None)
    res = dl.download_file("https://x/t.csv", str(tmp_path / "t.csv"), retries=2, backoff=0.0)
    assert res["ok"] is False
    assert "HTTP 500" in res["skipped_reason"]


def test_download_does_not_retry_on_403(tmp_path, monkeypatch):
    calls = {"n": 0}
    def auth_fail(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.HTTPError(req.full_url, 403, "forbidden", {}, None)
    monkeypatch.setattr(dl.urllib.request, "urlopen", auth_fail)
    monkeypatch.setattr(dl.time, "sleep", lambda *_: None)
    res = dl.download_file("https://x/t.csv", str(tmp_path / "t.csv"), retries=3, backoff=0.0)
    assert res["ok"] is False and calls["n"] == 1   # auth errors are terminal, no retry


def test_extract_tabular_tar(tmp_path):
    import io, tarfile, os
    import paperconan.fetch._download as dl
    # Build a small tar.gz with one xlsx-named member, one csv, one pdf (ignored)
    tar_path = tmp_path / "pkg.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        for name, body in [("PMC1/data1.xlsx", b"x"), ("PMC1/t.csv", b"a,b\n1,2\n"),
                           ("PMC1/fig.pdf", b"%PDF")]:
            data = body
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    out = tmp_path / "out"
    out.mkdir()
    extracted = dl._extract_tabular_tar(str(tar_path), str(out))
    names = sorted(os.path.basename(p) for p in extracted)
    assert names == ["data1.xlsx", "t.csv"]      # pdf dropped, paths flattened
    assert (out / "t.csv").read_bytes() == b"a,b\n1,2\n"
