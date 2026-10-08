# Source deployment preparation

This directory contains the native source build and disposable stack smoke
scripts. The platform checkout now has a reusable build/publication workflow,
but the fork's complete autodeploy pipeline is not installed or activated.
These local changes are not evidence of a working production deployment.

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
It rejects private packages and incomplete or ambiguous digest maps. Its release
artifact contains the source SHA, platform SHA, ARM64 platform and four digests.
Build and smoke still need to pass on a real runner. All four published runtime
images must pass the image security gates before SSH deploy. Only immutable
digests from that successful run may reach the deploy controller.

Production still needs a verified native CI release, bootstrap and daily backup
service installation, plus real container fault and offline restore tests. The platform has
service/timer definitions for the 20:15 UTC daily backup. They are not installed
or enabled on the VPS. Installer integration now renders the existing root-only
wrapper with the verified engine release, action `backup`, and app `penpot`.
It pauses an existing timer, rejects a running backup, and restores timer/files
on installation failure. Successful enrollment enables only the Penpot timer.
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
