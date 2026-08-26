# Bash completion for libvirt-backup-system.
#
# Installed to /usr/share/bash-completion/completions/ by
# ``libvirt-backup-system install``; the bash-completion package auto-loads
# it on first TAB. ``sudo libvirt-backup-system <TAB>`` also works because
# bash-completion's sudo handler re-dispatches to this registered function.
#
# Mirrors the fish completion: subcommands, per-subcommand flags, and
# dynamic VM-UUID / TIMESTAMP / host-id suggestions for restore, temp-restore
# restore, and du, backed by the same cached ``list-restore-points`` table
# (bash cannot render per-candidate descriptions, so values complete bare).

__lbs_subcommands="install add-node show-token update-config change-password uninstall check preflight doctor run backup start status log logs list-vms verify list-restore-points du restore temp-restore"

__lbs_cache_file() {
    local root
    if [ -n "${XDG_CACHE_HOME:-}" ]; then
        root="$XDG_CACHE_HOME"
    elif [ -n "${HOME:-}" ]; then
        root="$HOME/.cache"
    else
        root=/tmp
    fi
    printf '%s\n' "$root/libvirt-backup-system/restore-points.tsv"
}

__lbs_query_restore_points_uncached() {
    sudo -n libvirt-backup-system list-restore-points 2>/dev/null ||
        libvirt-backup-system list-restore-points 2>/dev/null
}

__lbs_refresh_restore_points_cache() {
    local cache tmp
    cache=$(__lbs_cache_file)
    tmp="$cache.$$.$RANDOM"
    mkdir -p "$(dirname "$cache")" 2>/dev/null || return 1
    if __lbs_query_restore_points_uncached >"$tmp" && [ -s "$tmp" ]; then
        mv -f "$tmp" "$cache"
    else
        rm -f "$tmp"
        return 1
    fi
}

__lbs_restore_cache_is_fresh() {
    local cache="$1" mtime now
    [ -f "$cache" ] || return 1
    mtime=$(stat -c %Y "$cache" 2>/dev/null) || return 1
    now=$(date +%s)
    [ $((now - mtime)) -lt 5 ]
}

# Print the cached list-restore-points table, refreshing it when stale.
# Matches the fish completion's caching contract: an existing cache is always
# usable (even if refresh fails, e.g. lapsed sudo token), and the first fill
# uses ``sudo -n`` so completion never prompts for a password mid-TAB.
__lbs_restore_points() {
    local cache
    cache=$(__lbs_cache_file)
    if [ -f "$cache" ]; then
        __lbs_restore_cache_is_fresh "$cache" || __lbs_refresh_restore_points_cache >/dev/null 2>&1
        cat "$cache" 2>/dev/null
        return 0
    fi
    __lbs_refresh_restore_points_cache >/dev/null 2>&1 && cat "$cache" 2>/dev/null
}

# Table columns: source-host-id vm-uuid timestamp run-id consistency vm-name.
__lbs_restore_uuids() {
    __lbs_restore_points | awk 'NR > 1 { print $2 }' | sort -u
}

__lbs_restore_timestamps_for_uuid() {
    __lbs_restore_points | awk -v u="$1" 'NR > 1 && $2 == u { print $3 }' | sort -ru
}

__lbs_du_hosts_and_uuids() {
    __lbs_restore_points | awk 'NR > 1 { print $1; print $2 }' | sort -u
}

__lbs_du_uuids_for_host() {
    __lbs_restore_points | awk -v h="$1" 'NR > 1 && $1 == h { print $2 }' | sort -u
}

# Options whose value is the next word (so the value is not a positional).
__lbs_option_takes_value() {
    case "$1" in
    --config | --prefix | --host-id | --run-id | --include-hosts | -n | --lines | \
        --kopia-password | --kopia-password-file | --kopia-password-env | \
        --new-kopia-password | --new-kopia-password-file | --new-kopia-password-env)
        return 0
        ;;
    esac
    return 1
}

__lbs_flags_for() {
    case "$1" in
    install) printf '%s\n' "--kopia-password --kopia-password-file --kopia-password-env --acknowledge-password-loss -y --yes --non-interactive --reinstall-deps" ;;
    change-password) printf '%s\n' "--new-kopia-password --new-kopia-password-file --new-kopia-password-env" ;;
    uninstall) printf '%s\n' "--purge-config --purge-state --purge-logs" ;;
    list-vms) printf '%s\n' "--json --include-blacklisted" ;;
    list-restore-points | du) printf '%s\n' "--json" ;;
    log | logs) printf '%s\n' "-f --follow -n --lines" ;;
    verify) printf '%s\n' "--include-hosts" ;;
    restore) printf '%s\n' "-v --verbose -y --yes --no-pre-backup --host-id --run-id" ;;
    temp-restore) printf '%s\n' "-v --verbose --host-id --run-id --json" ;;
    *) printf '%s\n' "" ;;
    esac
}

_libvirt_backup_system() {
    local cur prev sub i w skip positionals first_positional temp_sub
    COMPREPLY=()
    cur=${COMP_WORDS[COMP_CWORD]}
    prev=${COMP_WORDS[COMP_CWORD - 1]}

    # File completion for path-valued options.
    case "$prev" in
    --config | --prefix | --kopia-password-file | --new-kopia-password-file)
        COMPREPLY=($(compgen -f -- "$cur"))
        return 0
        ;;
    esac

    # Locate the subcommand and count the positional args typed after it,
    # skipping option values so flags never shift the positional slots.
    sub=""
    temp_sub=""
    skip=0
    positionals=0
    first_positional=""
    for ((i = 1; i < COMP_CWORD; i++)); do
        w=${COMP_WORDS[i]}
        if [ "$skip" = 1 ]; then
            skip=0
            continue
        fi
        if __lbs_option_takes_value "$w"; then
            skip=1
            continue
        fi
        case "$w" in
        -*) continue ;;
        esac
        if [ -z "$sub" ]; then
            sub="$w"
        elif [ "$sub" = temp-restore ] && [ -z "$temp_sub" ]; then
            temp_sub="$w"
        elif [ "$positionals" = 0 ]; then
            first_positional="$w"
            positionals=1
        else
            positionals=$((positionals + 1))
        fi
    done

    # No subcommand yet: complete subcommands (and the global flags on '-').
    if [ -z "$sub" ]; then
        case "$cur" in
        -*) COMPREPLY=($(compgen -W "--config --prefix -h --help" -- "$cur")) ;;
        *) COMPREPLY=($(compgen -W "$__lbs_subcommands" -- "$cur")) ;;
        esac
        return 0
    fi

    # Flag completion inside a subcommand.
    case "$cur" in
    -*)
        COMPREPLY=($(compgen -W "$(__lbs_flags_for "$sub") -h --help" -- "$cur"))
        return 0
        ;;
    esac

    # Positional completion.
    case "$sub" in
    restore)
        if [ "$positionals" = 0 ]; then
            COMPREPLY=($(compgen -W "$(__lbs_restore_uuids)" -- "$cur"))
        elif [ "$positionals" = 1 ]; then
            COMPREPLY=($(compgen -W "$(__lbs_restore_timestamps_for_uuid "$first_positional")" -- "$cur"))
        fi
        ;;
    temp-restore)
        if [ -z "$temp_sub" ]; then
            COMPREPLY=($(compgen -W "restore list stop remove" -- "$cur"))
        elif [ "$temp_sub" = restore ]; then
            if [ "$positionals" = 0 ]; then
                COMPREPLY=($(compgen -W "$(__lbs_restore_uuids)" -- "$cur"))
            elif [ "$positionals" = 1 ]; then
                COMPREPLY=($(compgen -W "$(__lbs_restore_timestamps_for_uuid "$first_positional")" -- "$cur"))
            fi
        fi
        ;;
    du)
        if [ "$positionals" = 0 ]; then
            COMPREPLY=($(compgen -W "$(__lbs_du_hosts_and_uuids)" -- "$cur"))
        elif [ "$positionals" = 1 ]; then
            COMPREPLY=($(compgen -W "$(__lbs_du_uuids_for_host "$first_positional")" -- "$cur"))
        fi
        ;;
    log | logs)
        COMPREPLY=($(compgen -W "run check maintenance maintenance-full verify all" -- "$cur"))
        ;;
    esac
    return 0
}

complete -F _libvirt_backup_system libvirt-backup-system
