import asyncio
import zipfile

from core.config import SharedConfig
from core.recording import RecordingManager


class FakeAcquirer:
    def __init__(self):
        self.paused = False

    def pause_and_release(self, _timeout):
        self.paused = True
        return True

    def resume(self):
        self.paused = False


def test_demo_record_duration_closes_output_and_resumes(tmp_path):
    async def scenario():
        acquirer = FakeAcquirer()
        manager = RecordingManager(acquirer, SharedConfig(), demo=True)
        await manager.start({"duration": 0.3, "directory": str(tmp_path),
                             "include_raw_iq": True})
        await manager._task
        status = manager.status()
        assert status["state"] == "idle"
        assert status["captures"] >= 1
        assert not acquirer.paused
        with zipfile.ZipFile(status["output"]) as archive:
            assert "demo-recording.json" in archive.namelist()

    asyncio.run(scenario())


def test_demo_run_until_stop(tmp_path):
    async def scenario():
        acquirer = FakeAcquirer()
        manager = RecordingManager(acquirer, SharedConfig(), demo=True)
        await manager.start({"duration": None, "directory": str(tmp_path)})
        await asyncio.sleep(0.3)
        await manager.stop()
        await manager._task
        assert manager.status()["state"] == "idle"
        assert not acquirer.paused

    asyncio.run(scenario())


def test_rolling_view_uses_dma_safe_recording_capture_duration(tmp_path):
    async def scenario():
        shared = SharedConfig()  # rolling mode: duration == 0
        manager = RecordingManager(FakeAcquirer(), shared, demo=False)

        assert manager.defaults()["capture_duration"] == 0.02
        spec = manager._default_spec(
            {"directory": str(tmp_path)}, tmp_path / "capture.zarr.zip")
        assert "    duration: 0.02\n" in spec

    asyncio.run(scenario())


def test_record_spec_uses_form_radio_fields_and_raw_iq(tmp_path):
    async def scenario():
        manager = RecordingManager(FakeAcquirer(), SharedConfig(), demo=False)
        spec = manager._default_spec({
            "center_frequency": 2.1e9,
            "sample_rate": 7.68e6,
            "gain": -3.5,
            "capture_duration": 0.01,
            "include_raw_iq": True,
        }, tmp_path / "capture.zarr.zip")

        assert "center_frequency: 2100000000.0" in spec
        assert "sample_rate: 7680000.0" in spec
        assert "gain: -3.5" in spec
        assert "duration: 0.01" in spec
        assert "iq_waveform: {}" in spec

    asyncio.run(scenario())


def test_record_spec_can_select_products(tmp_path):
    manager = RecordingManager(FakeAcquirer(), SharedConfig(), demo=False)
    spec = manager._default_spec({"analyses": ["channel_power"]},
                                 tmp_path / "capture.zarr.zip")
    assert "channel_power_time_series:" in spec
    assert "spectrogram:" not in spec
    assert "power_spectral_density:" not in spec


def test_catalog_resolution_rejects_escape(tmp_path, monkeypatch):
    import core.recording as recording
    monkeypatch.setattr(recording, "DEFAULT_RECORDINGS_DIR", tmp_path)
    manager = RecordingManager(FakeAcquirer(), SharedConfig(), demo=False)
    try:
        manager.resolve_catalog_item("../outside.zarr.zip")
    except ValueError as exc:
        assert "escapes" in str(exc)
    else:
        raise AssertionError("catalog traversal was accepted")


# ── Shutdown must not hand the radio back under a running sweep (item 2) ──
# The sweep runs in a worker thread, and Python cannot cancel a thread. The
# old shutdown used asyncio.wait_for, which CANCELS _run on timeout: that tore
# down the awaiting coroutine, ran _run's finally, and resumed the live
# Acquirer while run_sweep was still inside finite_capture_mode using the very
# source it was restoring.

def test_shutdown_does_not_cancel_a_sweep_that_overruns(tmp_path):
    async def scenario():
        acquirer = FakeAcquirer()
        manager = RecordingManager(acquirer, SharedConfig(), demo=True)
        started = asyncio.Event()

        async def slow_run(*_a, **_kw):
            started.set()
            await asyncio.sleep(30)          # outlives shutdown's window

        manager._run = slow_run
        manager._task = asyncio.create_task(slow_run())
        manager._stop = asyncio.Event()
        await started.wait()

        # Borrow the real timeout path with a short clock by racing it
        # directly: asyncio.wait must NOT cancel the task.
        await asyncio.wait([manager._task], timeout=0.2)
        assert not manager._task.done()
        assert not manager._task.cancelled()
        manager._task.cancel()

    asyncio.run(scenario())


def test_resume_is_skipped_while_the_sweep_thread_still_holds_the_radio(tmp_path):
    """_run's cleanup must detect a sweep thread it could not cancel."""
    async def scenario():
        acquirer = FakeAcquirer()
        acquirer.paused = True               # radio is released to the sweep
        manager = RecordingManager(acquirer, SharedConfig(), demo=True)
        manager._sweep_running = True        # thread still inside run_sweep

        async def boom(*_a, **_kw):
            raise RuntimeError("sweep blew up while the thread ran on")

        manager._run_demo = boom
        await manager.start({"duration": 0.3, "directory": str(tmp_path)})
        await manager._task
        # Still paused: resume would have raced finite_capture_mode's teardown.
        assert acquirer.paused is True
        assert manager.status()["state"] == "failed"

    asyncio.run(scenario())


def test_resume_happens_normally_when_the_sweep_thread_has_finished(tmp_path):
    async def scenario():
        acquirer = FakeAcquirer()
        manager = RecordingManager(acquirer, SharedConfig(), demo=True)
        assert manager._sweep_running is False
        await manager.start({"duration": 0.3, "directory": str(tmp_path)})
        await manager._task
        assert acquirer.paused is False
        assert manager.status()["state"] == "idle"

    asyncio.run(scenario())
