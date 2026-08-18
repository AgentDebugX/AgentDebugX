"""Streamlit annotation/visualisation app for GUI RCA results.

:mod:`~agentdebug.gui.vis.debugger_app` is a Streamlit script, not a library
module: importing it runs page setup and requires the ``gui-app`` extra
(``streamlit``, ``streamlit-adjustable-columns``, ``pandas``, ``pillow``). It is
therefore never imported from this package's ``__init__``.

Launch it against the installed copy with::

    streamlit run "$(python -c 'from agentdebug.gui.vis import app_path; print(app_path())')"

Run Streamlit from the directory holding your ``results/`` tree; the app
resolves all data paths against the current working directory.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ['app_path']


def app_path() -> Path:
    """Return the on-disk path of the Streamlit app, for ``streamlit run``."""
    return Path(__file__).resolve().parent / 'debugger_app.py'
