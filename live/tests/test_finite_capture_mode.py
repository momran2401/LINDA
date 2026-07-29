"""finite_capture_mode: the gapless→finite swap that makes recording survive.

The live viewer opens the radio gapless, where striqt raises on any receive
overflow and refuses retries.  A recording sweep overflows between captures by
construction, so the sweep has to run non-gapless.  These tests pin that
contract without needing striqt or hardware.
"""
import functools

import pytest

from core import shims


class FakeSpec:
    """Stands in for a frozen msgspec source spec (replace() → new object)."""

    def __init__(self, gapless=True, receive_retries=0, array_backend="cupy"):
        self.gapless = gapless
        self.receive_retries = receive_retries
        self.array_backend = array_backend

    def replace(self, **changes):
        fields = {"gapless": self.gapless,
                  "receive_retries": self.receive_retries,
                  "array_backend": self.array_backend}
        fields.update(changes)
        return FakeSpec(**fields)


class FakeSource:
    """Mimics the installed striqt source: setup_spec caches over __setup__."""

    def __init__(self, spec):
        self.__setup__ = spec

    @functools.cached_property
    def setup_spec(self):
        return self.__setup__

    def arm_spec(self, _capture):
        return None

    def _read_stream(self, *_a, **_kw):
        return 0, 0


@pytest.fixture
def source():
    return FakeSource(FakeSpec())


def test_swaps_gapless_off_and_enables_retries(source):
    live = source.setup_spec
    with shims.finite_capture_mode(source) as record:
        assert record.gapless is False
        assert record.receive_retries == 2
        # striqt re-reads setup_spec on every read_iq/arm_spec, so the cached
        # property must expose the swap, not the stale live spec.
        assert source.setup_spec is record
        assert source.__setup__ is record
    assert source.setup_spec is live
    assert source.setup_spec.gapless is True
    assert source.setup_spec.receive_retries == 0


def test_restores_live_spec_when_the_sweep_raises(source):
    live = source.setup_spec
    with pytest.raises(RuntimeError):
        with shims.finite_capture_mode(source):
            raise RuntimeError("sweep blew up")
    # The live viewer resumes on this source; it must be gapless again.
    assert source.setup_spec is live
    assert source.setup_spec.gapless is True


def test_leaves_an_already_finite_source_untouched():
    finite = FakeSpec(gapless=False, receive_retries=3)
    src = FakeSource(finite)
    with shims.finite_capture_mode(src) as spec:
        assert spec is finite
        assert src.setup_spec is finite
    assert src.setup_spec is finite


def test_honours_an_explicit_retry_count(source):
    with shims.finite_capture_mode(source, receive_retries=5) as record:
        assert record.receive_retries == 5


def test_honours_the_yaml_array_backend_request(source):
    """Regression: the recording YAML asks for cupy on hardware, but the live
    source spec hardcodes numpy and used to clobber the request — 4.6 s of
    CPU analysis per 20 ms capture on the AIR-T."""
    live = source.setup_spec
    assert live.array_backend == "cupy"   # FakeSpec default mimics the request path
    src = FakeSource(FakeSpec(array_backend="numpy"))
    with shims.finite_capture_mode(src, array_backend="cupy") as record:
        assert record.array_backend == "cupy"
        assert record.gapless is False
    assert src.setup_spec.array_backend == "numpy"   # restored for the viewer


def test_array_backend_none_leaves_the_live_backend_alone(source):
    with shims.finite_capture_mode(source, array_backend=None) as record:
        assert record.array_backend == source.__setup__.array_backend \
            or record.array_backend == "cupy"


def test_array_backend_swap_applies_even_when_already_finite():
    finite = FakeSpec(gapless=False, receive_retries=3, array_backend="numpy")
    src = FakeSource(finite)
    with shims.finite_capture_mode(src, array_backend="cupy") as record:
        assert record.array_backend == "cupy"
        assert record.gapless is False
        assert record.receive_retries == 3   # untouched — only backend changed
    assert src.setup_spec is finite


def test_registers_and_unregisters_the_swapped_spec(monkeypatch, source):
    """Sink path formatting resolves a radio ID by looking the sweep's source
    spec up in striqt's registry; an unregistered spec blocks, then raises."""
    registry = {}
    monkeypatch.setattr(shims, "_spec_registry", lambda: (registry, None))

    with shims.finite_capture_mode(source) as record:
        assert registry[record] is source
    assert record not in registry


def test_raises_when_the_source_exposes_no_spec():
    with pytest.raises(RuntimeError, match="no setup spec"):
        with shims.finite_capture_mode(object()):
            pass


def test_swap_failure_is_loud_not_silent():
    """A striqt build whose setup_spec is backed by state we don't control must
    fail here rather than silently recording under the live gapless spec —
    that silent-no-op mode is what hid the original overflow bug."""

    class Unswappable(FakeSource):
        def __init__(self, spec):
            self._spec = spec
            self.__setup__ = spec

        @property
        def setup_spec(self):        # read-only, ignores __setup__
            return self._spec

    src = Unswappable(FakeSpec())
    with pytest.raises(RuntimeError, match="did not take effect"):
        with shims.finite_capture_mode(src):
            pass


def test_missing_source_api_names_what_is_absent(source):
    assert shims.missing_source_api(source) == ()
    assert "arm_spec" in shims.missing_source_api(object())
