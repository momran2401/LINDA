"""Versioned, server-owned analysis and recording presets.

Each entry in `PRESETS` bundles a backend + nfft + analysis-target
configuration a user can apply in one step (e.g. from the web UI's preset
picker) instead of setting individual capture/analysis fields by hand.
`public_presets()` is the read-only, JSON-friendly view served to clients.
"""

PRESETS = {
    "spectrum-survey": {
        "version": 1, "label": "General spectrum survey",
        "description": "Calibrated-grid waterfall with robust default averaging.",
        "control": {"backend": "calibrated", "nfft": 1024,
                    "analysis": {"target": "spectrogram", "window": "kaiser, 11.88",
                                 "fractional_overlap": "13/28", "integration_bandwidth": "auto"}},
    },
    "narrowband-interferer": {
        "version": 1, "label": "Narrowband interferer",
        "description": "Fine frequency resolution and PSD peak statistics.",
        "control": {"backend": "psd", "nfft": 4096,
                    "analysis": {"target": "psd", "time_statistic": "mean, 0.95, max"}},
    },
    "wideband-occupancy": {
        "version": 1, "label": "Wideband occupancy",
        "description": "Fast survey grid paired with native power histograms.",
        "control": {"backend": "psd", "nfft": 512,
                    "analysis": {"target": "psd", "time_statistic": "mean, max"}},
    },
    "5g-cell-identification": {
        "version": 1, "label": "5G cell identification",
        "description": "SSB symbol grid plus low-rate PSS/SSS candidate detection.",
        "control": {"backend": "ssb",
                    "analysis": {"target": "ssb", "subcarrier_spacing": 30000,
                                 "sample_rate": 7680000, "discovery_periodicity": 0.02}},
        "cell_detection": True,
    },
    "dual-channel-comparison": {
        "version": 1, "label": "Dual-channel comparison",
        "description": "PSD mean/max statistics suitable for RX1−RX2 comparison.",
        "control": {"backend": "psd",
                    "analysis": {"target": "psd", "time_statistic": "mean, max"}},
    },
}


def public_presets():
    """Flatten PRESETS into a list suitable for a JSON API response.

    Returns:
        list[dict]: One dict per preset, each containing that preset's
        fields plus an "id" key holding its `PRESETS` dict key.
    """
    return [{"id": key, **value} for key, value in PRESETS.items()]
