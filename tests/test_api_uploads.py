"""Upload limits apply before the scorer copies a multipart file into memory."""

from unittest import mock

import pytest


@pytest.mark.parametrize("extra_bytes", [-1, 0, 1, 1024])
def test_score_bounds_upload_reads_and_accepts_the_exact_limit(monkeypatch, extra_bytes):
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    from starlette.datastructures import UploadFile

    from resume_radar import api

    # Exercise both sides of the boundary without allocating a 10 MB fixture.
    limit = 64
    monkeypatch.setattr(api, "MAX_UPLOAD_BYTES", limit)
    payload = b"Python Docker".ljust(limit + extra_bytes, b" ")
    reads = []
    original_read = UploadFile.read

    async def track_read(upload, size=-1):
        data = await original_read(upload, size)
        reads.append((size, len(data)))
        return data

    monkeypatch.setattr(UploadFile, "read", track_read)
    with mock.patch.object(api, "analyze", wraps=api.analyze) as analyze, \
         mock.patch.object(api.tempfile, "TemporaryDirectory",
                           wraps=api.tempfile.TemporaryDirectory) as temporary_directory, \
         fastapi_testclient.TestClient(api.app) as client:
        response = client.post(
            "/score", files={"file": ("resume.txt", payload, "text/plain")}
        )
        if extra_bytes > 0:
            assert response.status_code == 413
            assert response.json()["detail"] == "file larger than 10 MB"
            analyze.assert_not_called()
            temporary_directory.assert_not_called()
        else:
            assert response.status_code == 200
            assert response.json()["resume"] == "resume.txt"
            analyze.assert_called_once()
            temporary_directory.assert_called_once()

    # The handler must request bounded reads and copy at most one extra byte to
    # distinguish an exact-limit file from an oversized file, regardless of size.
    assert reads
    assert all(0 < size <= limit + 1 for size, _ in reads)
    assert sum(length for _, length in reads) <= limit + 1
