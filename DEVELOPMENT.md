# Development

How to work on libvirt-backup-system and verify changes locally. The most
important command in this file is the real-KVM end-to-end suite:

```bash
python -m tests.e2e --require-real-kvm
```

Run it on a Linux host with libvirt/KVM before trusting any change to the
backup or restore engine. Everything else (unit tests, typing, lint) runs
anywhere, including macOS — but only the e2e suite drives real `virsh`,
`qemu-nbd`, and `kopia` against real domains.

## Setup

Dependencies are managed with [uv](https://docs.astral.sh/uv/):

```bash
uv sync --locked --extra dev
```

Prefix the commands below with `uv run --locked --extra dev` (written as
`uv run` from here on) so they execute inside the locked environment.

## Everyday checks (any OS)

```bash
uv run python -m pytest            # unit suite (tests/unit)
uv run python -m tools.gates       # the full gate: format, lint, mypy,
                                   # pyright, coverage (fail-under 100%),
                                   # e2e, 300-line LOC gate
uv run python -m tools.gates --fix # same, but apply format/lint fixes first
```

`tools/install_hooks.py` installs a pre-push hook that runs the gate
automatically:

```bash
uv run python -m tools.install_hooks
```

On hosts without KVM the e2e step inside the gate skips with a printed
reason; on a capable host it runs for real.

## The real-KVM end-to-end suite

```bash
uv run python -m tests.e2e --require-real-kvm
```

### What it does

`tests.e2e` first probes the host for capability: `/dev/kvm` readable and
writable, `virsh` reachable at `qemu:///session`, and `virsh`, `qemu-img`,
`qemu-nbd`, `nbdcopy`, `kopia` on `PATH`. Without `--require-real-kvm` a
failed probe prints `SKIP real KVM e2e: <reason>` and exits 0; with the flag
a failed probe is a hard failure. Use the flag on any host that is *supposed*
to be capable (dev hypervisor, nightly CI) so a broken lane cannot silently
pass. Once the probe passes, two scenarios run in order, each against
throwaway domains under `qemu:///session` and a sandboxed `--prefix` install
in a temp dir — nothing touches the host's real config, VMs, or backups:

1. `tests/e2e/real_kvm_case.py` — the backup engine. Defines one running and
   one offline domain, installs into the sandbox, then drives `check`,
   `list-vms`, `run` (twice), `verify`, and a delete + `restore` + re-backup
   cycle. Asserts the kopia repo layout, snapshot tagging, the offline-VM
   skip, the post-restore dedup bound, and retention pruning.
2. `tests/e2e/temp_restore_case.py` — temp-restore clones and the overwrite
   guards. Keeps one "production" domain running throughout, backs it up,
   then: `temp-restore restore` boots a clone beside it (fresh UUID, fresh
   MAC, disks under the temp-restore state dir) while the prod disk's sha256
   stays untouched; `temp-restore list/stop/remove` walk the clone lifecycle
   and duplicate restores are refused; then the overwrite guards — `restore`
   without `-y` on a non-TTY stdin is refused, `restore -y` takes the default
   pre-restore safety backup (meta snapshot count grows by one), and
   `restore -y --no-pre-backup` skips it.

Both scenarios tear their domains and temp dirs down in `finally` blocks.

### Where to run it

On a Linux host (bare metal or a VM with nested KVM) with working
libvirt/QEMU and `/dev/kvm` access for your user. It deliberately uses
`qemu:///session`, so no root is needed. macOS and non-KVM hosts always
skip. To prepare a nested-KVM lab VM, see
[docs/testing-on-linux.md](docs/testing-on-linux.md).

### Prerequisites on Ubuntu and Debian

```bash
sudo apt update
sudo apt install -y \
  qemu-kvm \
  qemu-utils \
  libvirt-daemon-system \
  libvirt-clients \
  libnbd-bin
```

- `qemu-kvm` / `qemu-utils` — the emulator plus `qemu-img` and `qemu-nbd`.
- `libvirt-daemon-system` / `libvirt-clients` — libvirtd and `virsh`.
- `libnbd-bin` — provides `nbdcopy`, which streams disk data during backup.

`kopia` is not in the Debian/Ubuntu archives. Install the pinned release the
production installer uses (see
[Bundled binary install](docs/install.md#bundled-binary-install)) or place a
compatible binary at `/usr/local/bin/kopia`. The e2e probe requires it on
`PATH` before the sandboxed install runs.

Give your user access to `/dev/kvm` (log out and back in afterwards):

```bash
sudo adduser "$USER" kvm
```

Then confirm every probe the suite will make:

```bash
test -r /dev/kvm && test -w /dev/kvm && echo kvm ok
virsh -c qemu:///session uri
command -v virsh qemu-img qemu-nbd nbdcopy kopia
```

If any of these fails, `python -m tests.e2e` prints the exact missing
capability as its skip reason — fix that and re-run with
`--require-real-kvm`.

## Repository conventions

- Every authored text file stays at or under 300 lines (`tools/gates.py`
  enforces this); split modules rather than growing them.
- Unit coverage must stay at 100% (branch coverage, `fail_under = 100`).
- `ruff` (select ALL), strict `mypy`, and strict `pyright` all gate merges.
- CLI-facing behavior is documented in the `--help` text first
  (`cli_help*.py`); the markdown docs under `docs/` are secondary.
