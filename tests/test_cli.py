import pytest

from j5_macro import __version__
from j5_macro.cli.main import main


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_command_prints_help(capsys):
    assert main([]) == 0
    assert "usage: j5" in capsys.readouterr().out


def test_run_is_not_implemented_yet(capsys):
    assert main(["run", "script.yaml", "--step"]) == 2
    assert "not implemented" in capsys.readouterr().err
