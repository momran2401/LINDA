# live/legacy — pre-`core` frontends (frozen)

These four scripts predate the `live/core/` refactor. They are kept for
reference and are **not** part of the current frontend set — nothing in the
repo imports or launches them, and they receive no fixes or UI work.

| file | what it was |
|---|---|
| `striqt_standalone.py` | PyQt5 + pyqtgraph GUI; radio and display in one process |
| `pluto_standalone.py` | single-channel PlutoSDR variant of the above |
| `striqt_server_TCP.py` | headless server streaming frames over a socket (`:5005`) |
| `striqt_frontend_TCP.py` | PyQt6 + pyqtgraph client for that server |

Each carries its own copy of the radio/DSP/config code and imports `striqt`
directly — none of them reference `live/core/`, which is why they drifted.
Between them that is ~5.1k lines duplicating what `live/core/` now owns once.

## What replaced them

| legacy | current |
|---|---|
| `striqt_standalone.py`, `pluto_standalone.py` | `live/striqt_kiosk.py` — the web UI fullscreen on the local display |
| `striqt_server_TCP.py` | `live/striqt_web_server.py` |
| `striqt_frontend_TCP.py` | any browser pointed at that server |

The curses monitor (`live/striqt_standalone_terminal.py`) covers the
no-display / SSH case and is a thin frontend over `live/core/` like the rest.

Do not fix bugs here — fix them in `live/core/` and let every live frontend
inherit the fix. The historical rationale for these files (LV-*/P*-*
references) lives in `docs/`.
