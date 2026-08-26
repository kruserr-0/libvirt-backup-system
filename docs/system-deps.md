# System dependencies

The CLI shells out to `virsh`, `qemu-nbd`, `qemu-img`, `nbdcopy`, `df`, and
`kopia`. `kopia` is bundled by the installer at a pinned version (see
[Bundled binary install](install.md#bundled-binary-install)); `df` ships with
coreutils. The other four come from OS packages, and this page describes how
the tool handles them.

## What `install` does

`install` checks the dependencies **before touching anything**:

- It detects the Debian/Ubuntu release from `/etc/os-release` and maps any
  missing binaries onto the matching apt packages.
- Running interactively on a known release, it offers to run
  `apt-get update && apt-get install -y ...` for you after an explicit
  confirmation; `install -y`/`--yes` answers that confirmation
  automatically. When not already root, `sudo` asks for your password on the
  terminal; the credentials are used only for those apt-get commands and are
  dropped again (`sudo -k`) right after.
- For automation tooling, `--non-interactive` never prompts. Combined with
  `-y` it still attempts the apt install: as root it runs apt-get directly;
  as a normal user it uses `sudo -n` and **auto-skips the attempt** when
  passwordless sudo is not available (instead of hanging on a password
  prompt), then fails with the copy-paste command. `--non-interactive`
  without `-y` never touches apt — consent to install must be explicit.
- If you decline, the attempt was skipped, or the release is unknown, the
  install aborts **before anything is modified** and prints a copy-paste
  command instead. The tool never installs OS packages without your consent
  and never side-loads packages built for a different OS release. (Earlier
  versions side-loaded Debian-bookworm `.deb` files for `nbdcopy`, which
  could leave a foreign, half-broken binary on other releases; that path
  has been removed.)
- On a Debian/Ubuntu release newer than the ones this tool knows about, the
  error prints your detected OS version, the command that worked on earlier
  releases, and a ready-made question — naming the missing binaries and
  your exact version — that you can paste straight into Google or ChatGPT
  to get the install steps, e.g.:

  ```text
  How do I install the packages that provide virsh, nbdcopy on Debian GNU/Linux 14 (forky)?
  ```

Dependencies already on the system are never installed again — apt only ever
runs for the missing or broken ones.

## Repairing broken dependencies: `--reinstall-deps`

If the dependencies look present but misbehave for whatever reason, force a
clean reinstall of every dependency package (and a re-extract of the pinned
`kopia` binary), even when they look healthy:

```sh
sudo libvirt-backup-system install --reinstall-deps
```

This runs `apt-get install --reinstall -y libnbd-bin libvirt-clients
qemu-utils` after the usual confirmation (`-y` to skip it;
`--non-interactive -y --reinstall-deps` for automation, which needs root or
passwordless sudo).

## What `check` does

`check` validates the same binaries on every run — including that each one
**actually executes**, so a package with a missing shared library fails
`check` instead of silently passing — and prints the same apt hint when
something is missing. A missing `kopia` is reported with a pointer to re-run
`install`, which provides it.

## Installing by hand

### Debian 11 (bullseye), 12 (bookworm), 13 (trixie)

```sh
sudo apt-get update
sudo apt-get install -y libvirt-clients qemu-utils libnbd-bin
```

`libvirt-clients` provides `virsh`; `qemu-utils` provides `qemu-img` and
`qemu-nbd`; `libnbd-bin` provides `nbdcopy`.

### Ubuntu 20.04 (focal), 22.04 (jammy), 24.04 (noble)

```sh
sudo apt-get update
sudo apt-get install -y libvirt-clients qemu-utils libnbd-bin
```

### Anything newer or different

Try the command above first — the package names have been stable across
releases. If it fails on your release, search the web or ask an AI assistant
(Google/ChatGPT) how to install `virsh`, `qemu-nbd`, `qemu-img`, and
`nbdcopy` there, then re-run the install.
