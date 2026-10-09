#!/bin/bash
# Root-owned setup only: map the developer UID, repair owned storage, then drop
# privilege before interpreting any caller-supplied command.
set -euo pipefail

if [[ "$EUID" != 0 || "${SUDO_USER:-}" != penpot || $# -lt 2 ]]; then
    echo "devenv setup must be invoked by penpot through sudo" >&2
    exit 1
fi
uid="$1"
shift
if [[ ! "$uid" =~ ^[1-9][0-9]{0,9}$ ]] || (( uid > 2147483647 )); then
    echo "EXTERNAL_UID must be a nonroot developer UID between 1 and 2147483647" >&2
    exit 1
fi

exec 9>/run/penpot-devenv-setup.lock
/usr/bin/flock 9
if [[ -L /home/penpot || ! -d /home/penpot ]]; then
    echo "devenv home must be a directory, not a symlink" >&2
    exit 1
fi
old_uid="$(/usr/bin/id -u penpot)"
if [[ "$old_uid" != "$uid" ]]; then
    if /usr/bin/getent passwd "$uid" >/dev/null; then
        echo "EXTERNAL_UID is already assigned to another account" >&2
        exit 1
    fi
    # Prevent usermod from traversing/chowning the bind-mounted source tree.
    /usr/sbin/usermod -d /nonexistent penpot
    trap '/usr/sbin/usermod -d /home/penpot penpot' EXIT
    /usr/sbin/usermod -u "$uid" penpot
    /usr/sbin/usermod -d /home/penpot penpot
    trap - EXIT
    # These roots cannot be replaced by penpot; never follow nested symlinks.
    /usr/bin/chown -hRP penpot:users /opt/rustup /opt/emsdk /opt/uv
fi
/usr/bin/chown -h penpot:users /home/penpot
# Repair the home volume, but never recurse into the developer source mount.
shopt -s dotglob nullglob
for path in /home/penpot/*; do
    if [[ "$path" == /home/penpot/penpot ]]; then
        continue
    fi
    if [[ "$path" == /home/penpot/.config && ! -L "$path" ]]; then
        /usr/bin/chown -h penpot:users "$path"
        for config in "$path"/*; do
            if [[ "$config" == /home/penpot/.config/opencode ]] && /usr/bin/mountpoint -q "$config"; then
                continue
            fi
            /usr/bin/chown -hRP penpot:users "$config"
        done
    else
        /usr/bin/chown -hRP penpot:users "$path"
    fi
done
/usr/bin/flock -u 9
exec 9>&-
exec /usr/bin/setpriv --reuid="$uid" --regid="$(/usr/bin/id -g penpot)" \
    --init-groups --no-new-privs -- /usr/bin/env HOME=/home/penpot \
    PENPOT_DEVENV_SETUP_COMPLETE=1 EXTERNAL_UID="$uid" /home/entrypoint.sh "$@"
