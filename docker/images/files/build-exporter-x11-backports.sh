#!/usr/bin/env bash
set -euo pipefail

# Local APK revisions, not invented upstream releases. The upstream versions
# remain 1.8.13 and 0.9.12; r100 identifies Penpot's explicit CVE backports.
# Use Alpine's official archive helper for APKv2 checksums/control segments and
# Melange for package signatures; neither requires a privileged nested runner.
export SOURCE_DATE_EPOCH=1789538859
work=/tmp/exporter-x11-backports
key=/tmp/penpot-exporter-backports.rsa
mkdir -p "$work" /out

fetch() {
    local url=$1 sha256=$2 output=$3
    curl --fail --show-error --location "$url" --output "$output"
    printf '%s  %s\n' "$sha256" "$output" | sha256sum --check --status
}

fetch \
    https://raw.githubusercontent.com/alpinelinux/abuild/86b4ee3296ede77edb685f8c75bb32ca29e2ffe7/abuild-tar.c \
    7236cd707231db14b8939dad4b1bb54e37a06f79cc5be75a4919412a78e032aa \
    "$work/abuild-tar.c"
# This tool belongs only to the build stage.
cc -O2 "$work/abuild-tar.c" -o /usr/local/bin/abuild-tar $(pkg-config --cflags --libs openssl)
melange keygen "$key"
cp "$key.pub" /out/penpot-exporter-backports.rsa.pub
cp "$key.pub" /etc/apk/keys/penpot-exporter-backports.rsa.pub

build_package() {
    local package=$1 upstream=$2 version=$3 source_sha=$4 source_commit=$5
    local patch_commit=$6 patch_sha=$7 cve=$8
    local directory="$work/$package"
    local source_url="https://xorg.freedesktop.org/archive/individual/lib/$upstream-$version.tar.xz"
    local patch_url="https://gitlab.freedesktop.org/xorg/lib/$package/-/commit/$patch_commit.patch"
    local revision="$version-r100"
    local arch
    arch=$(apk --print-arch)
    mkdir -p "$directory/source" "$directory/root/usr/share/penpot-exporter-backports" "$directory/control"
    fetch "$source_url" "$source_sha" "$directory/source.tar.xz"
    fetch "$patch_url" "$patch_sha" "$directory/fix.patch"
    tar -xJf "$directory/source.tar.xz" --no-same-owner --strip-components=1 -C "$directory/source"
    (
        cd "$directory/source"
        patch --batch --forward -p1 < "$directory/fix.patch"
        if [[ "$package" == libx11 ]]; then
            ./configure --prefix=/usr --libdir=/usr/lib --disable-static \
                --disable-specs --disable-devel-docs --disable-thread-safety-constructor
        else
            ./configure --prefix=/usr --libdir=/usr/lib --disable-static
        fi
        make -j2
        make DESTDIR="$directory/root" install
    )
    # Ship shared runtime libraries/data, not development or build artifacts.
    rm -rf "$directory/root/usr/include" "$directory/root/usr/lib/pkgconfig" \
        "$directory/root/usr/share/man" "$directory/root/usr/share/doc"
    find "$directory/root/usr/lib" -name '*.la' -delete
    cp "$directory/fix.patch" "$directory/root/usr/share/penpot-exporter-backports/$package.patch"
    cat > "$directory/root/usr/share/penpot-exporter-backports/$package.json" <<EOF
{
  "package": "$package",
  "version": "$revision",
  "upstreamVersion": "$version",
  "upstreamCommit": "$source_commit",
  "source": "$source_url",
  "sourceSha256": "$source_sha",
  "patch": "$patch_url",
  "patchCommit": "$patch_commit",
  "patchSha256": "$patch_sha",
  "fixedCve": "$cve",
  "apkArchiveHelperCommit": "86b4ee3296ede77edb685f8c75bb32ca29e2ffe7"
}
EOF
    tar --format=pax --sort=name --owner=0 --group=0 --numeric-owner \
        --mtime="@$SOURCE_DATE_EPOCH" -cf "$directory/data.tar" -C "$directory/root" usr
    abuild-tar --hash --cut < "$directory/data.tar" > "$directory/hashed-data.tar"
    # Data streams retain a tar terminator; only APK control/signature streams
    # omit it so the next concatenated gzip segment can follow.
    dd if=/dev/zero bs=1024 count=1 >> "$directory/hashed-data.tar" 2>/dev/null
    gzip -n -9 < "$directory/hashed-data.tar" > "$directory/data.tar.gz"
    cat > "$directory/control/.PKGINFO" <<EOF
pkgname = $package
pkgver = $revision
pkgdesc = $upstream with Penpot backport of $cve ($patch_commit)
url = https://gitlab.freedesktop.org/xorg/lib/$package
builddate = $SOURCE_DATE_EPOCH
packager = Penpot <docker@penpot.app>
size = $(du -sb "$directory/root" | cut -f1)
arch = $arch
origin = $package
commit = $patch_commit
license = XFree86-1.1
# Source and patch checksums are retained in /usr/share/penpot-exporter-backports.
depend = glibc>=2.44-r8
EOF
    if [[ "$package" == libx11 ]]; then
        cat >> "$directory/control/.PKGINFO" <<EOF
depend = so:libxcb.so.1
provides = so:libX11.so.6=6
provides = so:libX11-xcb.so.1=1
EOF
    else
        cat >> "$directory/control/.PKGINFO" <<EOF
depend = libx11=1.8.13-r100
provides = so:libXrender.so.1=1
EOF
    fi
    printf 'datahash = %s\n' "$(sha256sum "$directory/data.tar.gz" | cut -d' ' -f1)" \
        >> "$directory/control/.PKGINFO"
    tar --format=ustar --owner=0 --group=0 --numeric-owner \
        --mtime="@$SOURCE_DATE_EPOCH" -cf - -C "$directory/control" .PKGINFO \
        | abuild-tar --cut | gzip -n -9 > "$directory/control.tar.gz"
    cat "$directory/control.tar.gz" "$directory/data.tar.gz" > "/out/$package-$revision.apk"
    melange sign --signing-key "$key" "/out/$package-$revision.apk"
    # Xrender must link against the same patched X11 runtime we will ship.
    apk add --no-cache "/out/$package-$revision.apk"
}

build_package libx11 libX11 1.8.13 \
    69606f485c2c07c14ef64f75b7bb326d48587af33795d9ab3e607c0b5f94f11c \
    6c75545a1deb51f5903992c52af6bc35cc9bc103 \
    6d4432b13c0b3b6a644c13de9c793a14db48d545 \
    dd9ecf20d2ca40c7ec3d8cdf3727c729f9fae3b6cc010e9436f152ae7cd6da31 \
    CVE-2026-88806
build_package libxrender libXrender 0.9.12 \
    b832128da48b39c8d608224481743403ad1691bf4e554e4be9c174df171d1b97 \
    46e12ff9e8e4d3f0313a2f097df93dbfdc14f92f \
    0564bce5446550c1b1681f3d8a933c20ea1455f7 \
    565e816e88ca223cebc6f833cfcfa63d20aaa22c8293d878bdd059910c4a66fc \
    CVE-2026-88807
# No private key crosses the build-stage boundary.
rm -f "$key" "$key.pub"
