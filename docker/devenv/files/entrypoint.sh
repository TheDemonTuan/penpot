#!/usr/bin/env bash

set -e

if [[ "${PENPOT_DEVENV_SETUP_COMPLETE:-}" != 1 ]]; then
    rm -f /tmp/penpot-devenv-ready
    exec sudo -n /usr/local/sbin/penpot-devenv-setup "${EXTERNAL_UID:-1000}" "$@"
fi
if [[ "$(id -u)" == 0 || "$(id -u)" != "${EXTERNAL_UID:-1000}" ]]; then
    echo "devenv runtime must run as the mapped developer UID" >&2
    exit 1
fi

# sudo's root setup uses a fixed secure_path; restore the developer toolchain
# only after the helper has dropped privileges.
export PATH="/home/penpot/.local/bin:/opt/jdk/bin:/opt/gh/bin:/opt/utils/bin:/opt/clojure/bin:/opt/node/bin:/opt/imagick/bin:/opt/cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

EMSDK_QUIET=1 . /opt/emsdk/emsdk_env.sh;

cp /usr/local/share/penpot/bashrc /home/penpot/.bashrc
cp /usr/local/share/penpot/vimrc /home/penpot/.vimrc
cp /usr/local/share/penpot/tmux.conf /home/penpot/.tmux.conf

# Seed SERENA_HOME with default config on first run.
mkdir -p "${SERENA_HOME}" /home/penpot/.local/bin /home/penpot/.cache/nginx
if [ ! -f "${SERENA_HOME}/serena_config.yml" ]; then
    cp /home/serena_config.yml "${SERENA_HOME}/serena_config.yml"
fi

rsync -ar /opt/cargo/ /home/penpot/.cargo/

# Generate a private, per-home-volume localhost identity, never an image secret.
tls_dir=/home/penpot/.config/penpot/tls
mkdir -p "$tls_dir"
chmod 700 "$tls_dir"
if [[ ! -s "$tls_dir/selfsigned.crt" || ! -s "$tls_dir/selfsigned.key" ]]; then
    (
        umask 077
        openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 365 \
            -subj /CN=localhost \
            -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1,IP:::1' \
            -keyout "$tls_dir/selfsigned.key" -out "$tls_dir/selfsigned.crt"
    )
fi
chmod 600 "$tls_dir/selfsigned.key"

export JAVA_OPTS="${JAVA_OPTS:--Djava.net.preferIPv4Stack=true}"
export PATH="/home/penpot/.cargo/bin:$PATH"
export CARGO_HOME="/home/penpot/.cargo"

export LANG=C.UTF-8
export LC_ALL=C.UTF-8
export COLORTERM=truecolor

touch /tmp/penpot-devenv-ready

exec "$@"
