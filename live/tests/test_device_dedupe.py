"""Enumeration dedupe: one physical radio must count as one radio.

A PlutoSDR raises a USB-ethernet gadget alongside its USB IIO interface, so
SoapySDR enumerates a single radio twice — `usb:3.2.5` and `ip:pluto.local`.
Two failures came out of that on a Pi 5, both fatal to a swap-and-go install:
`resolve_device` counted 2 matches and refused to start ("need exactly 1"),
and `_probe_device_facts` opened each row in turn so the second open hit
hardware the first had just claimed ("Unable to claim interface 3:2:5: Device
or resource busy (16)").

These tests pin the two properties that matter in opposite directions: a
duplicate must be dropped, and a radio must never be.
"""

from core.devices import _dedupe_same_radio


def _uris(rows):
    return [r.get("uri") for r in rows]


def test_one_pluto_two_rows_collapses_to_the_usb_row():
    """The exact enumeration observed on the radio host."""
    rows = _dedupe_same_radio([
        {"driver": "plutosdr", "label": "PlutoSDR #0 usb:3.2.5",
         "uri": "usb:3.2.5"},
        {"driver": "plutosdr", "label": "PlutoSDR #0 ip:pluto.local",
         "uri": "ip:pluto.local"},
    ])
    # USB wins: it is the path that works with no network configured, which is
    # the entire premise of hotspot and ethernet modes.
    assert _uris(rows) == ["usb:3.2.5"]


def test_network_only_pluto_survives():
    """No USB row to prefer ⇒ nothing is dropped.

    A Pluto reached only over the network is a legitimate configuration; the
    dedupe must never be the reason a radio vanishes from enumeration.
    """
    rows = _dedupe_same_radio([
        {"driver": "plutosdr", "uri": "ip:pluto.local"},
    ])
    assert _uris(rows) == ["ip:pluto.local"]


def test_two_plutos_on_two_usb_ports_stay_two_radios():
    """Distinct USB paths are distinct hardware, serial or no serial.

    The Pluto's Soapy driver reports no serial, so grouping by serial alone
    would collapse these into one and hide a radio the operator plugged in.
    """
    rows = _dedupe_same_radio([
        {"driver": "plutosdr", "uri": "usb:3.2.5"},
        {"driver": "plutosdr", "uri": "usb:1.4.1"},
    ])
    assert _uris(rows) == ["usb:3.2.5", "usb:1.4.1"]


def test_two_usb_plutos_plus_one_gadget_row():
    """Both USB radios kept; only the non-USB duplicate is dropped."""
    rows = _dedupe_same_radio([
        {"driver": "plutosdr", "uri": "usb:3.2.5"},
        {"driver": "plutosdr", "uri": "usb:1.4.1"},
        {"driver": "plutosdr", "uri": "ip:pluto.local"},
    ])
    assert _uris(rows) == ["usb:3.2.5", "usb:1.4.1"]


def test_repeated_identical_uri_is_a_real_duplicate():
    """The same URI twice is one device — dropping it stops the double-open."""
    rows = _dedupe_same_radio([
        {"driver": "plutosdr", "uri": "usb:3.2.5"},
        {"driver": "plutosdr", "uri": "usb:3.2.5"},
    ])
    assert _uris(rows) == ["usb:3.2.5"]


def test_other_drivers_are_untouched():
    """Only drivers known to multi-present are considered.

    A USRP and an AIR-T enumerate once each; nothing here may reorder or
    thin a list this function has no business judging.
    """
    original = [
        {"driver": "uhd", "serial": "31C92CD"},
        {"driver": "SoapyAIRT"},
        {"driver": "rtlsdr", "serial": "00000001"},
    ]
    assert _dedupe_same_radio(list(original)) == original


def test_rows_without_a_uri_are_kept():
    """A driver that reports no uri gives us nothing to prefer — keep both."""
    rows = _dedupe_same_radio([{"driver": "plutosdr"}, {"driver": "plutosdr"}])
    assert len(rows) == 2


def test_empty_enumeration():
    assert _dedupe_same_radio([]) == []
