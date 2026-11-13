
"""Tests for the command-line interface."""

import pytest

from wallpaper_orchestrator.cli import main


def test_default_message(capsys):
    result = main([])

    captured = capsys.readouterr()

    assert result == 0
    assert "under development" in captured.out


def test_version(capsys):
    with pytest.raises(SystemExit) as error:
        main(["--version"])

    captured = capsys.readouterr()

    assert error.value.code == 0
    assert "0.0.0" in captured.out
