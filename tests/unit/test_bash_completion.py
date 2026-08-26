from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

from libvirt_backup_system.bash_completion import (
    BASH_COMPLETION_DIR,
    BASH_COMPLETION_NAME,
    BASH_COMPLETION_SOURCE,
    bash_completion_target,
    install_bash_completion,
    remove_bash_completion,
)
from libvirt_backup_system.cli_parser import build_parser


def _completion_text() -> str:
    import libvirt_backup_system

    pkg_root = Path(libvirt_backup_system.__file__).resolve().parent
    return (pkg_root / "data" / BASH_COMPLETION_SOURCE).read_text(encoding="utf-8")


def _subparser_action(parser: argparse.ArgumentParser) -> argparse._SubParsersAction[argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise AssertionError("parser has no subparser action")


def _parser_visible_subcommands() -> set[str]:
    action = _subparser_action(build_parser())
    return {name for name in action.choices if name != "kopia-passthrough"}


def test_packaged_completion_file_exists() -> None:
    import libvirt_backup_system

    pkg_root = Path(libvirt_backup_system.__file__).resolve().parent
    assert (pkg_root / "data" / BASH_COMPLETION_SOURCE).is_file()


def test_completion_mentions_visible_argparse_subcommands() -> None:
    text = _completion_text()
    match = re.search(r'__lbs_subcommands="([^"]+)"', text)
    assert match is not None
    completed = set(match.group(1).split())
    assert _parser_visible_subcommands() <= completed


@pytest.mark.parametrize(
    ("command", "expected_options"),
    [
        (
            "install",
            {
                "--kopia-password",
                "--kopia-password-file",
                "--kopia-password-env",
                "--acknowledge-password-loss",
                "--yes",
                "--non-interactive",
                "--reinstall-deps",
            },
        ),
        ("change-password", {"--new-kopia-password", "--new-kopia-password-file", "--new-kopia-password-env"}),
        ("uninstall", {"--purge-config", "--purge-state", "--purge-logs"}),
        ("list-vms", {"--json", "--include-blacklisted"}),
        ("verify", {"--include-hosts"}),
        ("restore", {"--verbose", "--host-id", "--run-id", "--yes", "--no-pre-backup"}),
    ],
)
def test_completion_mentions_operator_visible_argparse_options(command: str, expected_options: set[str]) -> None:
    text = _completion_text()
    for option in expected_options:
        assert option in text, f"{option} missing from bash completion for {command}"


def test_bash_completion_target_lands_under_prefix(tmp_path: Path) -> None:
    target = bash_completion_target(tmp_path)
    assert target == tmp_path / str(BASH_COMPLETION_DIR).lstrip("/") / BASH_COMPLETION_NAME
    # bash-completion's lazy loader keys strictly on the command name.
    assert target.name == "libvirt-backup-system"


def test_install_writes_completion_into_prefix(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    install_bash_completion(tmp_path)
    target = bash_completion_target(tmp_path)
    assert target.is_file()
    assert "complete -F _libvirt_backup_system libvirt-backup-system" in target.read_text(encoding="utf-8")
    assert "installed bash completion" in capsys.readouterr().out


def test_install_swallows_oserror_on_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def refuse_copy(src: str, dst: str) -> str:
        raise OSError("read-only filesystem")

    monkeypatch.setattr("libvirt_backup_system.bash_completion.shutil.copyfile", refuse_copy)
    install_bash_completion(tmp_path)
    assert not bash_completion_target(tmp_path).exists()
    assert "bash completion install skipped" in capsys.readouterr().err


def test_install_warns_when_package_source_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bogus = tmp_path / "does-not-exist.bash"
    monkeypatch.setattr("libvirt_backup_system.bash_completion._packaged_completion_path", lambda: bogus)
    install_bash_completion(tmp_path)
    assert "bash completion source missing in package" in capsys.readouterr().err


def test_remove_returns_true_when_already_absent(tmp_path: Path) -> None:
    assert remove_bash_completion(tmp_path) is True


def test_remove_returns_true_after_removing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    install_bash_completion(tmp_path)
    capsys.readouterr()
    assert remove_bash_completion(tmp_path) is True
    assert not bash_completion_target(tmp_path).exists()
    assert "removed bash completion" in capsys.readouterr().out


def test_remove_returns_false_on_oserror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_bash_completion(tmp_path)
    capsys.readouterr()

    def refuse_unlink(self: Path, *, missing_ok: bool = False) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr("libvirt_backup_system.bash_completion.Path.unlink", refuse_unlink)
    assert remove_bash_completion(tmp_path) is False


def test_installer_installs_and_uninstall_removes_bash_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from libvirt_backup_system.installer import install, uninstall
    from tests.unit.conftest import write_kopia_password_file

    write_kopia_password_file(tmp_path)
    monkeypatch.setattr("libvirt_backup_system.installer.Path.exists", Path.exists)
    assert install(str(tmp_path)) == 0
    assert bash_completion_target(tmp_path).is_file()
    assert uninstall(str(tmp_path)) == 0
    assert not bash_completion_target(tmp_path).exists()
