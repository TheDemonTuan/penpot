# Source deployment preparation

## Decommission status (2026-10-15)

The live Penpot deployment on `oracle-main` (`design.tuannguyenviet.site`) has been
fully decommissioned per user request. All six runtime containers, unused database
images, persistent data volumes (`penpot_postgres_v15`, `penpot_assets`), daily
backup timers, deployment keys and host accounts have been permanently purged.
Cloudflare ingress and DNS records are removed. The repository workflows
`fork-deploy.yml` and `fork-ops.yml` remain disabled to prevent unintended re-deployment.

This directory contains the native source build and disposable stack smoke
scripts. The fork has the two guarded workflows pinned to the reviewed platform
release. Source scanning runs before native builds; SSH deployment stays disabled
until the production gates pass. Installed workflows do not prove deployment.
