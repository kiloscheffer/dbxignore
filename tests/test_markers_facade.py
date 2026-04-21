"""The markers facade must re-export three callables, platform-dispatched."""

from __future__ import annotations

import sys

import pytest


def test_markers_exports_three_callables():
    from dropboxignore import markers

    assert callable(markers.is_ignored)
    assert callable(markers.set_ignored)
    assert callable(markers.clear_ignored)


def test_markers_unsupported_platform_raises(monkeypatch):
    # Force a re-import under a fake platform by removing the cached module
    # and patching sys.platform before the import runs.
    monkeypatch.setattr(sys, "platform", "sunos5")
    monkeypatch.delitem(sys.modules, "dropboxignore.markers", raising=False)
    # Also remove the attribute from the package object so that
    # `from dropboxignore import markers` triggers a real re-import rather than
    # returning the stale module cached on the package (Python looks up
    # sub-module attributes on the parent package before checking sys.modules).
    import dropboxignore as _pkg
    monkeypatch.delattr(_pkg, "markers", raising=False)

    from dropboxignore import markers

    with pytest.raises(NotImplementedError, match="sunos5"):
        markers.is_ignored("/whatever")
