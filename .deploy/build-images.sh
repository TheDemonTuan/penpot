#!/usr/bin/env bash
# CI build stage only. Smoke tests and scans must precede any publication/deploy.
set -euo pipefail

fail() {
    printf 'penpot-build: %s\n' "$*" >&2
    exit 1
}

[[ $# == 0 ]] || fail "no arguments accepted"
[[ ${GITHUB_ACTIONS:-} == true ]] || fail "GitHub Actions runner required"
[[ ${GITHUB_REPOSITORY:-} == TheDemonTuan/penpot ]] || fail "wrong repository"
[[ ${GITHUB_REF:-} == refs/heads/main ]] || fail "main ref required"
[[ ${GITHUB_SHA:-} =~ ^[a-f0-9]{40}$ ]] || fail "full source SHA required"
[[ ${GITHUB_RUN_ID:-} =~ ^[1-9][0-9]*$ ]] || fail "numeric run ID required"
[[ ${GITHUB_RUN_ATTEMPT:-} =~ ^[1-9][0-9]*$ ]] || fail "numeric run attempt required"

cd "$(git rev-parse --show-toplevel)"
[[ $(git rev-parse HEAD) == "$GITHUB_SHA" ]] || fail "checkout SHA mismatch"
[[ -z $(git status --porcelain --untracked-files=all) ]] || fail "clean checkout required"
case "$(uname -m)" in
    arm64|aarch64) ;;
    *) fail "native ARM64 host required; emulation is not release proof" ;;
esac
case "$(docker info --format '{{.Architecture}}')" in
    arm64|aarch64) ;;
    *) fail "native ARM64 Docker daemon required" ;;
esac
docker buildx version >/dev/null

readonly source_url="https://github.com/TheDemonTuan/penpot"
readonly revision="sha-$GITHUB_SHA"
readonly volume="penpot-ci-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT-$$"
export DEVENV_TAG="ci-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT-$revision"
export PENPOT_USER_DATA_VOLUME="$volume"
export BUILD_WASM=yes BUILD_STORYBOOK=no

labels=(--label "org.opencontainers.image.revision=$GITHUB_SHA"
        --label "org.opencontainers.image.source=$source_url")
cache=()
if [[ -n ${ACTIONS_RUNTIME_TOKEN:-} && -n ${ACTIONS_RESULTS_URL:-} ]]; then
    cache=(--cache-from "type=gha,version=2,scope=penpot-devenv-arm64"
           --cache-to "type=gha,version=2,scope=penpot-devenv-arm64,mode=max")
fi

verify_image() {
    local actual
    actual=$(docker image inspect --format \
        '{{.Architecture}} {{index .Config.Labels "org.opencontainers.image.revision"}} {{index .Config.Labels "org.opencontainers.image.source"}}' "$1")
    [[ $actual == "arm64 $GITHUB_SHA $source_url" ]] || fail "image architecture/source/revision mismatch: $1"
}

docker buildx build --platform linux/arm64 --load \
    "${cache[@]}" "${labels[@]}" \
    --tag "penpotapp/devenv:$DEVENV_TAG" \
    --file docker/devenv/Dockerfile docker/devenv
verify_image "penpotapp/devenv:$DEVENV_TAG"

# Never use or remove the developer's penpotdev_user_data volume.
if docker volume inspect "$volume" >/dev/null 2>&1; then
    fail "run volume already exists"
fi
docker volume create --label penpot.ci=true \
    --label "penpot.ci.run=$GITHUB_RUN_ID/$GITHUB_RUN_ATTEMPT" "$volume" >/dev/null
cleanup() {
    local result=$?
    trap - EXIT
    if ! docker volume rm "$volume" >/dev/null; then
        printf 'penpot-build: failed to remove owned run volume %s\n' "$volume" >&2
        [[ $result != 0 ]] || result=1
    fi
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

for role in frontend backend exporter mcp; do
    ./manage.sh "build-$role-bundle"
    [[ -s bundles/$role/version.txt ]] || fail "missing $role source bundle"
    mkdir -p "docker/images/bundle-$role"
    rsync -a --delete "bundles/$role/" "docker/images/bundle-$role/"
    image="ghcr.io/thedemontuan/penpot-$role:$revision"
    docker buildx build --platform linux/arm64 --load "${labels[@]}" \
        --build-arg "BUNDLE_PATH=./bundle-$role/" \
        --tag "$image" --file "docker/images/Dockerfile.$role" docker/images
    verify_image "$image"
done
printf 'Built four local ARM64 images for %s; not scanned, published, or deployed.\n' "$GITHUB_SHA"
