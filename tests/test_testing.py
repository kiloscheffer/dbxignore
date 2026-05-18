"""Unit tests for the DBXIGNORE_TEST_FAIL_* failure-injection primitives."""

from __future__ import annotations

import errno
import logging

import pytest

from dbxignore import _testing


def test_fail_point_active_false_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DBXIGNORE_TEST_FAIL_SAMPLE", raising=False)
    assert _testing.fail_point_active("SAMPLE") is False


def test_fail_point_active_true_and_warns_when_env_set(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("DBXIGNORE_TEST_FAIL_SAMPLE", "1")
    with caplog.at_level(logging.WARNING, logger="dbxignore._testing"):
        assert _testing.fail_point_active("SAMPLE") is True
    assert any("SAMPLE" in r.message for r in caplog.records)


def test_fail_point_active_false_when_env_empty_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicitly empty value is treated as unset — only a non-empty
    value arms the fail point, matching the manual-test scripts' `=1` form."""
    monkeypatch.setenv("DBXIGNORE_TEST_FAIL_SAMPLE", "")
    assert _testing.fail_point_active("SAMPLE") is False


def test_raise_if_fail_point_noop_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DBXIGNORE_TEST_FAIL_SAMPLE", raising=False)
    # Must not raise.
    _testing.raise_if_fail_point("SAMPLE")


def test_raise_if_fail_point_raises_default_enotsup_and_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("DBXIGNORE_TEST_FAIL_SAMPLE", "1")
    with (
        caplog.at_level(logging.WARNING, logger="dbxignore._testing"),
        pytest.raises(OSError) as excinfo,
    ):
        _testing.raise_if_fail_point("SAMPLE")
    assert excinfo.value.errno == errno.ENOTSUP
    assert any("SAMPLE" in r.message for r in caplog.records)


def test_raise_if_fail_point_raises_supplied_exc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DBXIGNORE_TEST_FAIL_SAMPLE", "1")
    custom = OSError(errno.EIO, "custom injected error")
    with pytest.raises(OSError) as excinfo:
        _testing.raise_if_fail_point("SAMPLE", custom)
    assert excinfo.value is custom
