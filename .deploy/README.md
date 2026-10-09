# Source deployment preparation

This directory contains the native source build and disposable stack smoke
scripts. The fork has the two guarded workflows pinned to the reviewed platform
release. Source scanning runs before native builds; SSH deployment stays disabled
until the production gates pass. Installed workflows do not prove deployment.

## Build contract

Run `bash .deploy/build-images.sh` from a clean checkout in GitHub Actions.
The script requires:

- Repository `TheDemonTuan/penpot`, ref `refs/heads/main`, and a full
  `GITHUB_SHA` equal to the checked-out commit.
- Numeric `GITHUB_RUN_ID` and `GITHUB_RUN_ATTEMPT`.
- Native ARM64 host and Docker daemon; Docker Buildx and host `rsync`.
- Enough disk and memory to build the local devenv, WASM, and four bundles.

It builds `penpotapp/devenv` locally, without pulling the upstream devenv,
then uses the existing `manage.sh` bundle commands. The build forces WASM
on and Storybook off. Each run gets its own home/cache volume, which is
removed on success or failure. Existing volumes are not reused or removed.

The four local images are tagged:

```text
ghcr.io/thedemontuan/penpot-frontend:sha-<source-sha>
ghcr.io/thedemontuan/penpot-backend:sha-<source-sha>
ghcr.io/thedemontuan/penpot-exporter:sha-<source-sha>
ghcr.io/thedemontuan/penpot-mcp:sha-<source-sha>
```

Each image must report ARM64 and the expected source/revision labels.
These tags are local build output, **not registry digest release evidence**.
The script has no registry login, push, SSH, or host mutation step.

Public Debian, nginx, and Node base images are pinned to OCI index digests
that include `linux/arm64`. Apt updates still require network access and
change over time. Pinning a base does not replace a final-image scan.

The frontend wraps only its nginx command with `nginx-safe-run.py`. Real nginx
upstream-failure reproduction exposed query tokens in stderr even though the
access format omits queries/referrers. The wrapper drains stderr, strips query
and fragment data from request targets/URLs, and redacts credential values while
keeping path, severity and upstream cause. It forwards TERM/INT/QUIT, reaps nginx
and preserves its exit status. Invalid sanitization or a line over 1 MiB stops
the child and fails without printing raw input. Other command overrides keep
their existing dispatch. Native ARM64 CI must still prove the exact frontend
image's success/404/refused/timeout behavior and shared edge log safety.

## Source security gate

Keep the source HIGH/CRITICAL gate and all four image gates enabled. Regenerate
dependency locks with the repository-pinned pnpm after updating affected packages;
use frozen installs before building. Plugins use the workspace pnpm lock, not the
removed, stale `plugins-runtime/package-lock.json`.

`braces@3.0.3` remains blocked by `CVE-2026-93687`: no patched release exists at
the time of this cutover. Stable Eleventy, nodemon and stylelint dependency chains
still require it. Do not add an ignore, relabel a local patch as a fixed version,
or replace the stable documentation toolchain with an unverified alpha release.
A published fix or a verified compatible dependency migration is required before
the full source/build/runtime/publication chain can pass.

Devenv now runs as the mapped nonroot developer user after restricted ownership
setup. Its TLS key is generated in the home volume, not shipped in the image;
see the development guide for trust and renewal. Verify the actual Docker startup
and four native bundle builds on the authorized disposable ARM64 runner. Shell
checks and local TLS generation do not replace that runtime proof.



## Required work before activation

The platform checkout now contains the strict four-digest request contract,
six-service Compose definition, route adapters, image/source checks,
four-image state/status, application-only start/stop, and snapshot validation
helpers. It also saves protected transaction intent and all three route forms,
checks route hashes, and confirms maintenance before stopping writers. Failed
maintenance acknowledgement restores the old route without stopping writers;
failed restoration keeps the intent for recovery. The candidate stage now
records its snapshot before migration and restores verified data on migration
failure. It recreates only the Penpot database and streams the assets archive.
Before public exposure, it saves the target release and a durable committed
flag. Recovery after that boundary restarts the target, never restores old data.
Crash reconciliation checks saved route forms and resumes old or target according
to that boundary. Before reopening the old release after restore, it records a
resuming checkpoint so another crash cannot replay the dump over new writes.
The transaction holds one app lock throughout its stages. Locked backups preserve
the current source and generation, save their own recoverable intent, and retain
seven complete snapshots plus operation references and previous-release sources.
The root backup command, deploy/reconcile dispatch, and adoption are wired behind
the same activation guard. The root-only offline restore command is implemented
with check/apply modes, a safety snapshot, and resumable checkpoints. It reports
the restored data timestamp without exposing runtime secrets. App SSH reconcile
cannot take over an offline restore operation. Before the exposure boundary,
restore keeps writers stopped; after it, recovery completes forward without
replaying the dump. Password rotation sends secrets through stdin after separate
PostgreSQL logging settings, never through command arguments.
Enrollment preflight now checks all six live services without pulling images or
starting containers. It requires the requested source SHA on every application
image, root-owned canonical config, complete Compose parity, non-root app users,
no published ports or extra capabilities, exact volume mount modes and resource
limits, owned isolated networks with only the expected members, and fresh
frontend `200/OK` readiness. It compares runtime environment values against the
pinned image defaults plus Compose, so extra SMTP settings or changed flags fail
closed. These checks are covered by local fake-Docker tests, not native runtime
proof. The root bootstrap now checks exact revisions, all four anonymous image
refs, certificate hostname/trust/expiry, mounted edge configuration, query-log
policy, existing resources and shared route collisions. Its guarded apply path
stages the immutable engine, preserves secrets on retry, creates owned storage,
persists the internal edge attachment and starts the six services without builds.
It publishes maintenance first, checks the candidate, then calls installer
check/apply. Failed exposure acknowledgement closes the public route again.
The same engine activation fence still blocks this apply path before any write;
these local checks do not authorize production activation.

Until the complete engine is ready, the controller rejects Penpot deployment and
adoption with `PENPOT_RELEASE_ENGINE_NOT_READY`; it cannot fall back to blue-green. The six-service smoke stack and recovery tests remain.
The reusable platform workflow builds and smokes on native ARM64 without
package-write permission. It transfers the exact tested images by a same-run
artifact ID and checksum to a separate publication job. That job verifies all
four source labels, ARM64 identities and non-root users before pushing SHA tags.
It pushes and resolves all four immutable digests before starting anonymous
checks, so a first publication creates every package even if GHCR defaults them
to private. Push, malformed/ambiguous digest and anonymous-check failures retain
their original error codes and cannot create a release artifact. An anonymous
failure alone does not distinguish private visibility from a network failure.
The CI summary provides visibility guidance without registry responses, auth
URLs or credentials. If the packages are private, change **all four** packages
to Public at <https://github.com/users/TheDemonTuan/packages>, then rerun only the
failed publication job using the build artifact from that same run. If it has
expired, rerun the complete chain; never combine digests from different runs or
grant the workflow an admin PAT. The strict release artifact remains
`schemaVersion/sourceSha/platformRef/platform/images`, with four immutable refs.

The platform's `scripts/write-penpot-proof.py --directory "$RUNNER_TEMP/penpot-proof"`
records evidence separately from release publication. It reads `SOURCE_SHA`,
`PLATFORM_REF`, `GITHUB_RUN_ID` and `GITHUB_RUN_ATTEMPT`; the two run identifiers
are decimal strings. `proof.json` contains `schemaVersion: 1`, those exact source,
platform and run identities, `platform: "linux/arm64"`, and a `files` object
mapping every evidence-relative path to its full SHA-256. It requires a passing
source-bound `smoke.json`, identity-bound `lifecycle.json`, and at least one
sanitized `logs/*.log` (or `runtime.log`). Lifecycle failure reports may still
be recorded as diagnostic evidence; a proof file is **not** release approval.
The writer rejects symlinks, unexpected files, oversized/non-UTF-8 evidence,
raw inspect environment fields, recognizable credentials, private keys and
credential/query URLs. Report producers must sanitize at collection time; the
writer rejects suspicious content, never rewrites raw logs into public evidence.
Never place runtime environments, dumps, keys or raw container inspect output
in the evidence directory. Existing derived proof is removed before validating
replacement evidence, so rejection cannot retain a stale manifest.

Native workflow integration must run the lifecycle harness after source smoke
and before saving source tags, then run the proof writer even after harness
failure when reports exist. Upload only `proof.json`, `smoke.json`,
`lifecycle.json`, `logs/*.log` and optional `runtime.log`, only when proof writing
succeeds, as `penpot-proof-<sourceSha>-<runId>-<runAttempt>` with 30-day retention.
Do not upload the report directory wholesale. Save/upload the original four
source tags and continue publication only after successful smoke, lifecycle and
proof generation. Keep native build `contents: read`, with `packages: write`
only on the separate publication job. All four published runtime images must
pass image security gates before SSH deploy. Build, smoke and lifecycle proof
still need to pass on a real runner; only immutable digests from that successful
run may reach the deploy controller.

Production still needs a verified native CI release, bootstrap and daily backup
service installation, plus real container fault and offline restore tests. The platform has
service/timer definitions for the 20:15 UTC daily backup. They are not installed
or enabled on the VPS. Installer integration now renders the existing root-only
wrapper with the verified engine release, action `backup`, and app `penpot`.
It pauses an existing timer, rejects a running backup, and restores timer/files
on installation failure. Successful enrollment also restores its prior timer
state; fresh enrollment leaves it disabled/inactive until public readiness,
a complete production backup and a private ARM64 restore rehearsal pass.
Image-only rollback
is not safe. Bootstrap, forced-command SSH, ingress changes, and old OpenDesign
retirement must wait for the relevant verification gates in the plan.

Fork workflow templates live under `.deploy/workflows/`, not the active
`.github/workflows/` directory. They fence the repository and `main` branch,
share a deployment lock with `queue: max`, gate build on source scans, and require
all four digest scans before deployment. SSH deployment stays off unless the
repository variable `PENPOT_DEPLOY_ENABLED` is exactly `true`. Status/reconcile
use the same lock; image-only rollback is not offered. GitHub allows at most
100 pending runs and orders them by when they enter the queue, not by push time.
`cancel-in-progress: false` alone would still replace pending runs, so both
workflows explicitly request the larger queue. GitHub documents this setting at
https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency.
Actionlint does not yet recognize `queue`; the renderer checks the exact shared
queue policy itself, then removes only that setting from temporary linter copies.
The generated workflows retain `queue: max`; all other syntax stays subject to
normal actionlint checks.

Every job in the 25 upstream workflows requires `github.repository == 'penpot/penpot'`
as well as its prior condition. This protects the enable-to-disable window,
including schedules and `pull_request_target`; it does not replace disabling
those workflows through GitHub. Keep fork Actions disabled during the initial
push, then enable only `fork-deploy.yml` and `fork-ops.yml`. Do not dispatch an
upstream workflow to force indexing. Keep `PENPOT_DEPLOY_ENABLED=false` through
build proof, bootstrap, private restore rehearsal and public Web/export/MCP checks.

Render the templates only after the final platform commit passes CI on `main`:

```bash
python3 .deploy/render-workflows.py \
  --platform-checkout ../vps-deploy \
  --platform-ref <successful-platform-commit-sha> \
  --actionlint /path/to/current-actionlint
```

The renderer rejects mutable refs, dirty or mismatched platform checkouts,
missing implementation files, failed/missing platform CI, non-main source
branches, linter errors and conflicting existing fork workflows. Deployment
binds the downloaded same-run artifact ID, source SHA, platform SHA and complete
image map to the build outputs before the forced-command SSH transport.

To repin an existing pair, add `--replace-platform-ref <old-40-hex-sha>` while
rendering the new verified platform SHA on local `main`. Both existing files must
be regular files and match the current templates rendered at the old SHA exactly.
A custom edit, missing file, mixed pair or symlink fails before either file changes.
Both files already at the new SHA are idempotent. Without the flag, the renderer
keeps its original refusal to replace different content. Do not delete workflows
to bypass review; lint both candidate files before replacing the pair.

## Scoped ingress controller

The platform's manual `penpot-ingress.yml` workflow now offers `survey` and
`apply` on central repository `main` only. Apply uses the `platform-admin`
environment and existing central Cloudflare/admin SSH secrets; it does not
export credentials to the fork or publish the origin. Before any Cloudflare
write, pinned SSH checks the installed platform SHA, coherent healthy Penpot
state, certificate trust/hostname/expiry and the cloudflared origin-CA mount.
The engine activation fence remains in place, so preparation code cannot make
this origin proof pass before activation.

Apply preserves shared tunnel entries/options, DNS metadata, cache rules and
other Access domains/policies. It refuses wildcard Access, foreign DNS, route
collisions and changed inventories. It removes exact-design Access last and
checks the origin again before each Access change. Missing responses can be
retried; it never rolls back a whole shared tunnel or ruleset. Immediate rereads
are drift checks, not a server-side transaction across Cloudflare resources;
operators must avoid concurrent dashboard edits during apply. A successful
receipt also requires public HTTPS `/readyz` to return exactly `200/OK`, without
following Access redirects. Local fake-API and fake-SSH tests do not prove these
changes on the real account. No ingress apply has run yet.

No fork GitHub workflow is installed here yet. Keep inherited fork Actions
disabled until only the guarded fork workflows are ready. The current local
preparation does not change live containers, DNS, or secrets. Platform source
changes remain local and the Penpot host binding is not active.

## Local contract checks

```bash
python3 -m unittest discover -s scripts -p test_penpot_build.py -v
python3 -m unittest discover -s scripts -p test_penpot_smoke.py -v
python3 -m unittest discover -s scripts -p test_penpot_workflows.py -v
shellcheck .deploy/build-images.sh .deploy/smoke-stack.sh
bash -n .deploy/build-images.sh .deploy/smoke-stack.sh
git diff --check
```

The Python tests use a disposable Git checkout and fake Docker commands.
They test script guards, build arguments, error propagation, owned-volume
cleanup, pinned base references, runtime user declarations, and the nginx
access-log format. Smoke-script checks also test CI fences, disposable resource
names, zero-port Compose generation and JSON/SSE MCP response handling. The real
smoke script starts six services, checks frontend HTML and 200/OK readiness,
creates an active throwaway account through the private CLI, and checks MCP
initialize/tools/list through the frontend proxy. It removes only its own stack
and labeled disposable volumes; it never runs on the VPS.

The local tests do **not** prove image builds, nginx startup, application health,
export rendering, MCP transport, or query-free runtime error logs. Real container
execution is still required. MCP initialize/tools/list is not proof that an
agent can read or change a focused page; that needs public-path acceptance.
