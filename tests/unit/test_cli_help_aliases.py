from __future__ import annotations

import pytest

from libvirt_backup_system.cli import main


@pytest.mark.parametrize("argv", [[], ["help"], ["?"]])
def test_cli_top_level_help_aliases(argv: list[str], capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert output.startswith("usage: libvirt-backup-system")
    assert "Common workflows:" in output


def test_cli_blank_argv_from_process_shows_help(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.argv", ["libvirt-backup-system"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    assert capsys.readouterr().out.startswith("usage: libvirt-backup-system")
