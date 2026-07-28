"""pull_recordings: the laptop-side mirror of the radio's recording catalog.

The tool's job is to never leave a half-file looking like a finished recording,
and to never treat the login page as data. These pin both.
"""
import importlib.util
import io
import zipfile
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "pull_recordings.py"
_spec = importlib.util.spec_from_file_location("pull_recordings", _MODULE_PATH)
pull = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pull)


def _zip_bytes(payload=b"recording payload"):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("data", payload)
    return buffer.getvalue()


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False


class FakeClient:
    """Serves canned bytes per recording id."""

    def __init__(self, blobs):
        self.blobs = blobs
        self.requested = []

    def catalog(self):
        return [{"id": rid, "bytes": len(blob), "state": "complete"}
                for rid, blob in self.blobs.items()]

    def open_download(self, recording_id):
        self.requested.append(recording_id)
        return FakeResponse(self.blobs[recording_id])


def test_downloads_and_verifies(tmp_path):
    blob = _zip_bytes()
    client = FakeClient({"air8201b/a.zarr.zip": blob})
    item = {"id": "air8201b/a.zarr.zip", "bytes": len(blob), "state": "complete"}

    assert pull.download_one(client, item, tmp_path) == "downloaded"

    landed = tmp_path / "air8201b" / "a.zarr.zip"
    assert landed.read_bytes() == blob
    assert list(tmp_path.rglob("*.part")) == []


def test_second_run_skips_what_is_already_here(tmp_path):
    blob = _zip_bytes()
    client = FakeClient({"air8201b/a.zarr.zip": blob})
    item = {"id": "air8201b/a.zarr.zip", "bytes": len(blob), "state": "complete"}

    assert pull.download_one(client, item, tmp_path) == "downloaded"
    assert pull.download_one(client, item, tmp_path) == "skipped"
    # The skip must not cost a transfer.
    assert client.requested == ["air8201b/a.zarr.zip"]


def test_truncated_transfer_leaves_nothing_behind(tmp_path):
    """A short read must not produce a file the next run would call complete."""
    blob = _zip_bytes()
    client = FakeClient({"air8201b/a.zarr.zip": blob[: len(blob) // 2]})
    item = {"id": "air8201b/a.zarr.zip", "bytes": len(blob), "state": "complete"}

    assert pull.download_one(client, item, tmp_path) == "failed"
    assert not (tmp_path / "air8201b" / "a.zarr.zip").exists()
    assert list(tmp_path.rglob("*.part")) == []


def test_corrupt_archive_is_rejected(tmp_path):
    """Right length, wrong content — e.g. an HTML error page of equal size."""
    corrupt = b"<html>not a zip at all........</html>"
    client = FakeClient({"air8201b/a.zarr.zip": corrupt})
    item = {"id": "air8201b/a.zarr.zip", "bytes": len(corrupt), "state": "complete"}

    assert pull.download_one(client, item, tmp_path) == "failed"
    assert not (tmp_path / "air8201b" / "a.zarr.zip").exists()


def test_corrupt_archive_is_accepted_when_verification_is_off(tmp_path):
    corrupt = b"<html>not a zip at all........</html>"
    client = FakeClient({"air8201b/a.zarr.zip": corrupt})
    item = {"id": "air8201b/a.zarr.zip", "bytes": len(corrupt), "state": "complete"}

    assert pull.download_one(client, item, tmp_path, verify=False) == "downloaded"


def test_a_short_local_file_is_replaced_not_skipped(tmp_path):
    blob = _zip_bytes()
    landed = tmp_path / "air8201b" / "a.zarr.zip"
    landed.parent.mkdir(parents=True)
    landed.write_bytes(blob[:4])          # torn file from an older, cruder run

    client = FakeClient({"air8201b/a.zarr.zip": blob})
    item = {"id": "air8201b/a.zarr.zip", "bytes": len(blob), "state": "complete"}

    assert pull.download_one(client, item, tmp_path) == "downloaded"
    assert landed.read_bytes() == blob


def test_in_progress_recordings_are_not_fetched(tmp_path):
    """A recording still being written is listed as partial; leave it alone."""
    blob = _zip_bytes()

    class PartialClient(FakeClient):
        def catalog(self):
            return [{"id": "air8201b/live.zarr.zip", "bytes": len(blob),
                     "state": "partial"}]

    client = PartialClient({"air8201b/live.zarr.zip": blob})
    assert pull.fetch_complete(client, tmp_path) == (0, 0, 0)
    assert client.requested == []


@pytest.mark.parametrize("code,expected", [
    (401, "--user"),
    (403, "--user"),
    (303, "authentication required"),
    (404, "does not look like a radio"),
])
def test_error_messages_explain_themselves(code, expected):
    import urllib.error
    exc = urllib.error.HTTPError("http://radio/x", code, "nope", {}, None)
    assert expected in pull.describe_connection_error(exc, "http://radio")


def test_download_url_quotes_each_path_segment():
    opened = []

    class Recorder(pull.RadioClient):
        def _open(self, path):
            opened.append(path)
            return FakeResponse(b"")

    Recorder("http://radio").open_download("air 8201b/a b.zarr.zip")
    # The slash between radio id and filename is structural and must survive;
    # everything else has to be escaped.
    assert opened == ["/recordings/air%208201b/a%20b.zarr.zip/download"]
