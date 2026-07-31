"""Field-scoped readback + superseded-state tests."""
import numpy as np

from core import state
from core.acquisition import Acquirer
from core.config import SharedConfig
from core.operations import OPERATIONS


def make_acquirer():
    state.configure_device("demo")   # 2-channel profile; no hardware touched
    shared = SharedConfig()
    return shared, Acquirer(shared)   # never started — unit-level only


def test_display_only_op_skips_readback_entirely():
    _, acq = make_acquirer()
    op = OPERATIONS.begin("config", "rows → 40")
    OPERATIONS.set_fields(op, ["rows"])
    # No device/source access must happen: _readback_and_verify returns
    # "success" before ever calling the adapter.
    verdict = acq._readback_and_verify(object(), op)
    assert verdict == "success"
    stages = [s["stage"] for s in OPERATIONS.get(op)["stages"]]
    assert "readback" in stages
    detail = next(s["detail"] for s in OPERATIONS.get(op)["stages"]
                  if s["stage"] == "readback")
    assert "not applicable" in detail


def test_fields_recorded_on_config_ops():
    state.configure_device("air8201b")
    shared = SharedConfig()
    ack = shared.update({"center": 2000e6, "rows": 24})
    fields = OPERATIONS.fields(ack["op_id"])
    assert set(fields) == {"center", "rows"}
    state.configure_device("demo")


def test_unknown_fields_mean_full_check():
    _, acq = make_acquirer()
    op = OPERATIONS.begin("radio", "open")   # no set_fields → full recipe
    assert OPERATIONS.fields(op) is None


def test_superseded_is_a_distinct_terminal_state():
    state.configure_device("air8201b")
    shared = SharedConfig()
    a1 = shared.update({"center": 1900e6})
    a2 = shared.update({"center": 1910e6})   # supersedes op 1 before hardware
    op1 = OPERATIONS.get(a1["op_id"])
    assert op1["state"] == "superseded"
    op2 = OPERATIONS.get(a2["op_id"])
    assert op2["state"] == "running"
    state.configure_device("demo")


# ── Gain verdicts (audit item 9) ──────────────────────────────────────────
# `cfg.gain` is striqt's CALIBRATED gain; most drivers report a raw composite
# gain on a different scale. Judging them against each other collapsed every
# config op on a healthy radio to "mismatch", because verdict_state treats any
# mismatched field as fatal. An incomparable gain must read "we did not verify
# this", not "the radio disagreed".

def _demo_adapter():
    from core.devices.base import DeviceAdapter
    state.configure_device("demo")

    class _A(DeviceAdapter):
        name = "demo"
    return _A()


def test_incomparable_gain_is_unverified_not_mismatch():
    from core.operations import verdict_state
    adapter = _demo_adapter()
    cfg = SharedConfig().snapshot()
    cfg.gain = 0.0
    # Driver reports a composite gain nowhere near the calibrated request.
    actuals = {"center": cfg.center, "sample_rate": cfg.sample_rate,
               "gain": [42.0, 42.0]}
    verdicts = adapter.verify(cfg, actuals)
    gains = [v for v in verdicts if v["field"].startswith("gain")]
    assert gains, "gain must still be reported"
    assert all(v["state"] == "readback_unsupported" for v in gains)
    # What the driver said is still carried, so the op log can show it.
    assert all(v["actual"] == 42.0 for v in gains)
    # Center and rate agreed, so the operation as a whole is verified — not
    # dragged to "mismatch" by a number that was never comparable.
    assert verdict_state(verdicts) == "verified"


def test_comparable_gain_is_judged_for_real():
    from core.operations import verdict_state
    adapter = _demo_adapter()
    adapter.gain_readback_comparable = True
    cfg = SharedConfig().snapshot()
    cfg.gain = 0.0
    ok = adapter.verify(cfg, {"center": cfg.center,
                              "sample_rate": cfg.sample_rate,
                              "gain": [0.1, 0.1]})
    assert verdict_state(ok) == "verified"
    bad = adapter.verify(cfg, {"center": cfg.center,
                               "sample_rate": cfg.sample_rate,
                               "gain": [9.0, 9.0]})
    assert verdict_state(bad) == "mismatch"


def test_center_mismatch_still_fatal():
    # The gain change must not have softened the checks that matter.
    from core.operations import verdict_state
    adapter = _demo_adapter()
    cfg = SharedConfig().snapshot()
    verdicts = adapter.verify(cfg, {"center": cfg.center + 5e6,
                                    "sample_rate": cfg.sample_rate,
                                    "gain": [0.0, 0.0]})
    assert verdict_state(verdicts) == "mismatch"
