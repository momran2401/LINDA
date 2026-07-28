"""Recording handoff safety: resuming the live radio, and catalog cost.

These cover the two ways a recording used to damage the *viewer* rather than
just itself — an AIR-T closed during resume (its AD9371 management sensors go
with it for the life of the process) and a catalog listing that CRC-scans every
archive on the request path.
"""
from core import acquisition, recording, state
from core.acquisition import Acquirer
from core.config import SharedConfig


def _acquirer(device):
    state.configure_device(device)
    return Acquirer(SharedConfig())


def test_resume_rearm_retries_on_the_same_deepwave_device(monkeypatch):
    """AIR-T activation can transiently report XDMA EBUSY right after the
    recorder releases it; that must be retried, never escalated to a reopen."""
    acq = _acquirer("air8201b")
    attempts = []

    def flaky(_cfg, _op_id=None):
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("XDMA busy")

    monkeypatch.setattr(acq, "rearm", flaky)
    assert acq._resume_rearm(acq.shared.snapshot(), attempts=5) is True
    assert len(attempts) == 3


def test_resume_rearm_gives_up_without_closing_a_deepwave_device(monkeypatch):
    closed = []
    acq = _acquirer("air8201b")

    def always_fails(_cfg, _op_id=None):
        raise RuntimeError("still busy")

    monkeypatch.setattr(acq, "rearm", always_fails)
    monkeypatch.setattr(acquisition, "close_source", lambda _s: closed.append(1))

    assert acq._resume_rearm(acq.shared.snapshot(), attempts=2) is False
    # _resume_rearm itself must never destroy the device singleton; the caller
    # decides, and for Deepwave models it keeps retrying instead.
    assert closed == []


def test_resume_rearm_takes_one_attempt_on_other_radios(monkeypatch):
    acq = _acquirer("pluto")
    attempts = []

    def always_fails(_cfg, _op_id=None):
        attempts.append(1)
        raise RuntimeError("nope")

    monkeypatch.setattr(acq, "rearm", always_fails)
    assert acq._resume_rearm(acq.shared.snapshot()) is False
    assert len(attempts) == 1


def test_resume_rearm_stops_early_when_paused_again(monkeypatch):
    """A second recording starting mid-resume must not be fought over."""
    acq = _acquirer("air8201b")
    attempts = []

    def fails_then_pause(_cfg, _op_id=None):
        attempts.append(1)
        acq._pause_requested.set()
        raise RuntimeError("busy")

    monkeypatch.setattr(acq, "rearm", fails_then_pause)
    assert acq._resume_rearm(acq.shared.snapshot(), attempts=5) is False
    assert len(attempts) == 1


def _write_archive(path, payload=b"x" * 2048):
    import zipfile
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("data", payload)


def test_catalog_skips_crc_verification_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(recording, "DEFAULT_RECORDINGS_DIR", tmp_path)
    _write_archive(tmp_path / "air8201b" / "20260728T000000Z.zarr.zip")

    manager = recording.RecordingManager(None, SharedConfig(), demo=True)
    rows = manager.catalog()

    assert len(rows) == 1
    # "not checked", not "checked and fine" — the listing must not read every
    # archive end to end just to render the Record tab.
    assert rows[0]["valid"] is None
    assert rows[0]["entries"] == 1
    assert rows[0]["state"] == "complete"


def test_catalog_can_verify_on_request(tmp_path, monkeypatch):
    monkeypatch.setattr(recording, "DEFAULT_RECORDINGS_DIR", tmp_path)
    _write_archive(tmp_path / "air8201b" / "20260728T000001Z.zarr.zip")

    manager = recording.RecordingManager(None, SharedConfig(), demo=True)
    assert manager.catalog(verify=True)[0]["valid"] is True


def test_catalog_flags_a_corrupt_archive_when_verifying(tmp_path, monkeypatch):
    monkeypatch.setattr(recording, "DEFAULT_RECORDINGS_DIR", tmp_path)
    path = tmp_path / "air8201b" / "20260728T000002Z.zarr.zip"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"this is not a zip file")

    manager = recording.RecordingManager(None, SharedConfig(), demo=True)
    assert manager.catalog(verify=True)[0]["valid"] is False
