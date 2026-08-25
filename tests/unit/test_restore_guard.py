"""Tests for the overwrite-restore guards (confirmation + pre-restore backup)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from libvirt_backup_system import kopia_snapshots, restore, restore_guard
from libvirt_backup_system.manifest import MANIFEST_FILENAME
from libvirt_backup_system.shell import CommandError, CommandResult

from .conftest import ALPHA_UUID
from .restore_helpers import TIMESTAMP, make_config, make_manifest, make_row, ok_result, rows_result


class _Stdin:
    def __init__(self, *, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def _allowed(tmp_path: Path, **overrides: bool) -> bool:
    kwargs = {"assume_yes": False, "pre_backup": True}
    kwargs.update(overrides)
    return restore_guard.overwrite_allowed(make_config(tmp_path), "myvm", ALPHA_UUID, **kwargs)


def test_assume_yes_and_no_pre_backup_allows_without_prompting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(restore_guard, "run", lambda *_a, **_k: pytest.fail("must not touch virsh"))
    assert _allowed(tmp_path, assume_yes=True, pre_backup=False) is True
    assert "pre-restore safety backup skipped" in capsys.readouterr().err


def test_confirmation_refused_when_stdin_is_not_a_tty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "stdin", _Stdin(tty=False))
    assert _allowed(tmp_path) is False
    assert "refusing overwrite restore without confirmation" in capsys.readouterr().err


@pytest.mark.parametrize("answer", ["yes", "YES", " y "])
def test_confirmation_accepts_yes_answers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    monkeypatch.setattr(sys, "stdin", _Stdin(tty=True))
    monkeypatch.setattr("builtins.input", lambda: answer)
    assert _allowed(tmp_path, pre_backup=False) is True


@pytest.mark.parametrize("answer", ["", "no", "n", "yes please"])
def test_confirmation_rejects_other_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], answer: str
) -> None:
    monkeypatch.setattr(sys, "stdin", _Stdin(tty=True))
    monkeypatch.setattr("builtins.input", lambda: answer)
    assert _allowed(tmp_path) is False
    err = capsys.readouterr().err
    assert "OVERWRITE VM 'myvm'" in err
    assert "overwrite restore cancelled by operator" in err


def test_confirmation_treats_eof_as_no(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_eof() -> str:
        raise EOFError

    monkeypatch.setattr(sys, "stdin", _Stdin(tty=True))
    monkeypatch.setattr("builtins.input", raise_eof)
    assert _allowed(tmp_path) is False


@pytest.mark.parametrize(
    "raises",
    [CommandError(CommandResult(["virsh"], 1, "", "boom")), OSError("no virsh")],
    ids=["command-error", "os-error"],
)
def test_pre_backup_fails_when_domstate_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], raises: Exception
) -> None:
    def boom(_args: list[str], **_: Any) -> CommandResult:
        raise raises

    monkeypatch.setattr(restore_guard, "run", boom)
    assert _allowed(tmp_path, assume_yes=True) is False
    assert "pre-restore backup could not read VM state" in capsys.readouterr().err


def test_pre_backup_refuses_offline_vm_with_actionable_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(restore_guard, "run", lambda args, **_: ok_result(args, "shut off\n"))
    assert _allowed(tmp_path, assume_yes=True) is False
    err = capsys.readouterr().err
    # The refusal must tell the operator what to do (--no-pre-backup), why the
    # backup cannot be taken, and what accepting that permanently gives up.
    assert "cannot take the pre-restore safety backup: the VM is not running" in err
    assert "only a running VM can be snapshotted" in err
    assert "re-run with --no-pre-backup" in err
    assert "everything the VM wrote after its most recent backup" in err


def test_pre_backup_aborts_when_backup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(restore_guard, "run", lambda args, **_: ok_result(args, "running\n"))
    monkeypatch.setattr(restore_guard, "backup_vm", lambda _c, _vm: False)
    assert _allowed(tmp_path, assume_yes=True) is False
    assert "pre-restore safety backup failed" in capsys.readouterr().err


def test_pre_backup_aborts_when_backup_rejects_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(_c: Any, _vm: Any) -> bool:
        raise ValueError("refusing unsafe VM name")

    monkeypatch.setattr(restore_guard, "run", lambda args, **_: ok_result(args, "running\n"))
    monkeypatch.setattr(restore_guard, "backup_vm", refuse)
    assert _allowed(tmp_path, assume_yes=True) is False
    assert "pre-restore safety backup rejected VM identity" in capsys.readouterr().err


def test_pre_backup_passes_running_vm_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured: dict[str, Any] = {}

    def fake_backup(_config: Any, vm: Any) -> bool:
        captured["vm"] = vm
        return True

    monkeypatch.setattr(restore_guard, "run", lambda args, **_: ok_result(args, "running\n"))
    monkeypatch.setattr(restore_guard, "backup_vm", fake_backup)
    assert _allowed(tmp_path, assume_yes=True) is True
    assert captured["vm"].name == "myvm"
    assert captured["vm"].uuid == ALPHA_UUID
    assert "pre-restore safety backup completed" in capsys.readouterr().out


def test_restore_aborts_overwrite_when_guard_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The overwrite branch of ``restore()`` must stop before any destructive
    step when ``overwrite_allowed`` returns False, and must forward the
    operator's flag choices to the guard."""
    cfg = make_config(tmp_path)
    row = make_row(tmp_path)
    manifest = make_manifest()

    def write_meta(**kwargs: Any) -> None:
        (kwargs["dest"] / MANIFEST_FILENAME).write_text(manifest.to_json(), encoding="utf-8")

    monkeypatch.setattr(restore, "enumerate_backups_result", lambda _c, *, vm_uuid=None: rows_result([row]))
    monkeypatch.setattr(kopia_snapshots, "snapshot_restore_to_path", write_meta)
    monkeypatch.setattr(restore, "run", lambda args, **_: ok_result(args, "myvm\n" if "domname" in args else ""))
    monkeypatch.setattr(restore, "_restore_overwrite", lambda *_a, **_k: pytest.fail("must not reach overwrite"))
    captured: dict[str, Any] = {}

    def refuse(_config: Any, vm_name: str, vm_uuid: str, *, assume_yes: bool, pre_backup: bool) -> bool:
        captured.update({"vm_name": vm_name, "vm_uuid": vm_uuid, "assume_yes": assume_yes, "pre_backup": pre_backup})
        return False

    monkeypatch.setattr(restore, "overwrite_allowed", refuse)
    assert restore.restore(cfg, ALPHA_UUID, TIMESTAMP, assume_yes=True, pre_backup=False) == 1
    assert captured == {"vm_name": "myvm", "vm_uuid": ALPHA_UUID, "assume_yes": True, "pre_backup": False}
