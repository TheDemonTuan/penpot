#!/usr/bin/env python3
"""Build-script contract tests; fake Docker is not runtime smoke evidence."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".deploy/build-images.sh"
SOURCE = "https://github.com/TheDemonTuan/penpot"

DOCKER = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ["CALLS"], "a") as out:
    out.write(json.dumps(["docker", *args]) + "\n")
if args[:1] == ["info"]:
    print(os.environ.get("DOCKER_ARCH", "aarch64"))
elif args[:2] == ["volume", "inspect"]:
    sys.exit(0 if os.environ.get("VOLUME_EXISTS") == "1" else 1)
elif args[:2] == ["volume", "rm"]:
    sys.exit(23 if os.environ.get("FAIL_CLEANUP") == "1" else 0)
elif args[:2] == ["image", "inspect"]:
    print(os.environ.get("IMAGE_ARCH", "arm64"), os.environ.get("IMAGE_SHA", os.environ["GITHUB_SHA"]),
          "https://github.com/TheDemonTuan/penpot")
elif args[:2] == ["buildx", "build"]:
    if os.environ.get("FAIL_BUILD") == "1":
        sys.exit(17)
'''

MANAGE = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
with open(os.environ["CALLS"], "a") as out:
    out.write(json.dumps(["manage", *sys.argv[1:],
                         os.environ["DEVENV_TAG"],
                         os.environ["PENPOT_USER_DATA_VOLUME"],
                         os.environ["BUILD_WASM"],
                         os.environ["BUILD_STORYBOOK"]]) + "\n")
role = sys.argv[1].removeprefix("build-").removesuffix("-bundle")
if os.environ.get("FAIL_BUNDLE") == role:
    sys.exit(19)
bundle = Path("bundles") / role
bundle.mkdir(parents=True)
(bundle / "version.txt").write_text("2.18.3\n")
'''


class BuildContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.calls = self.root / "calls.jsonl"
        self.env = dict(os.environ, GITHUB_ACTIONS="true",
                        GITHUB_REPOSITORY="TheDemonTuan/penpot",
                        GITHUB_REF="refs/heads/main", GITHUB_RUN_ID="1234",
                        GITHUB_RUN_ATTEMPT="2", CALLS=str(self.calls),
                        PATH=f"{self.bin}:{os.environ['PATH']}")
        for key in ("FAIL_BUILD", "FAIL_BUNDLE", "IMAGE_SHA", "DOCKER_ARCH",
                    "VOLUME_EXISTS", "FAIL_CLEANUP", "IMAGE_ARCH",
                    "ACTIONS_RUNTIME_TOKEN", "ACTIONS_CACHE_URL",
                    "ACTIONS_RESULTS_URL"):
            self.env.pop(key, None)
        (self.repo / ".deploy").mkdir()
        shutil.copyfile(SCRIPT, self.repo / ".deploy/build-images.sh")
        self.executable(self.repo / "manage.sh", MANAGE)
        self.executable(self.bin / "docker", DOCKER)
        self.executable(self.bin / "uname", "#!/bin/sh\necho aarch64\n")
        self.git("init", "--initial-branch=main")
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                 "commit", "-m", "test fixture")
        self.git("-c", "tag.gpgSign=false", "tag", "2.18.3")
        self.env["GITHUB_SHA"] = self.git("rev-parse", "HEAD").strip()

    @staticmethod
    def executable(path, content):
        path.write_text(content)
        path.chmod(0o755)

    def git(self, *args):
        return subprocess.check_output(
            ["git", "-c", "commit.gpgSign=false", "-c", "core.hooksPath=/dev/null", *args],
            cwd=self.repo, stderr=subprocess.DEVNULL, text=True)

    def run_build(self):
        return subprocess.run(["bash", ".deploy/build-images.sh"], cwd=self.repo,
                              env=self.env, text=True, capture_output=True)

    def events(self):
        if not self.calls.exists():
            return []
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def test_rejects_wrong_repository_ref_sha_and_non_ci_before_docker(self):
        for key, value in (("GITHUB_ACTIONS", "false"),
                           ("GITHUB_REPOSITORY", "penpot/penpot"),
                           ("GITHUB_REF", "refs/heads/develop"),
                           ("GITHUB_SHA", "0" * 40),
                           ("GITHUB_RUN_ID", "bad/id")):
            with self.subTest(key=key):
                previous = self.env[key]
                self.env[key] = value
                result = self.run_build()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.events(), [])
                self.env[key] = previous

    def test_rejects_dirty_and_untracked_source(self):
        (self.repo / "manage.sh").write_text("modified")
        self.assertNotEqual(self.run_build().returncode, 0)
        self.assertEqual(self.events(), [])
        self.git("restore", "manage.sh")
        (self.repo / "untracked").write_text("untracked")
        self.assertNotEqual(self.run_build().returncode, 0)
        self.assertEqual(self.events(), [])

    def test_rejects_non_native_host_before_docker(self):
        self.executable(self.bin / "uname", "#!/bin/sh\necho x86_64\n")
        self.assertNotEqual(self.run_build().returncode, 0)
        self.assertEqual(self.events(), [])

    def test_rejects_wrong_daemon_without_mutation(self):
        self.env["DOCKER_ARCH"] = "x86_64"
        self.assertNotEqual(self.run_build().returncode, 0)
        self.assertEqual([event[1] for event in self.events()], ["info"])

    def test_builds_four_roles_same_sha_and_cleans_only_owned_volume(self):
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        events = self.events()
        builds = [e for e in events if e[:3] == ["docker", "buildx", "build"]]
        self.assertEqual(len(builds), 5)
        for build in builds:
            self.assertIn("--load", build)
            self.assertIn("linux/arm64", build)
            self.assertNotIn("--push", build)
            self.assertIn(f"org.opencontainers.image.revision={self.env['GITHUB_SHA']}", build)
        bundles = [e for e in events if e[0] == "manage"]
        self.assertEqual([e[1] for e in bundles],
                         [f"build-{r}-bundle" for r in ("frontend", "backend", "exporter", "mcp")])
        self.assertTrue(all(e[-2:] == ["yes", "no"] for e in bundles))
        self.assertEqual(len({e[3] for e in bundles}), 1)
        self.assertTrue(bundles[0][3].startswith("penpot-ci-1234-2-"))
        self.assertEqual(events[-1], ["docker", "volume", "rm", bundles[0][3]])
        for role in ("frontend", "backend", "exporter", "mcp"):
            self.assertTrue(any(f"ghcr.io/thedemontuan/penpot-{role}:sha-{self.env['GITHUB_SHA']}" in e
                                for e in builds))
        self.assertFalse(any("--cache-to" in e for e in builds))

    def test_build_failure_stops_before_bundles_and_does_not_delete_volume(self):
        self.env["FAIL_BUILD"] = "1"
        self.assertEqual(self.run_build().returncode, 17)
        events = self.events()
        self.assertFalse(any(e[0] == "manage" for e in events))
        self.assertFalse(any(e[:3] == ["docker", "volume", "rm"] for e in events))

    def test_bundle_failure_propagates_and_cleans_volume(self):
        self.env["FAIL_BUNDLE"] = "backend"
        self.assertEqual(self.run_build().returncode, 19)
        events = self.events()
        self.assertEqual(len([e for e in events if e[0] == "manage"]), 2)
        self.assertEqual(events[-1][:3], ["docker", "volume", "rm"])

    def test_existing_volume_is_not_reused_or_removed(self):
        self.env["VOLUME_EXISTS"] = "1"
        self.assertNotEqual(self.run_build().returncode, 0)
        events = self.events()
        self.assertFalse(any(e[0] == "manage" for e in events))
        self.assertFalse(any(e[:3] == ["docker", "volume", "rm"] for e in events))

    def test_cleanup_failure_reports_failure_after_successful_build(self):
        self.env["FAIL_CLEANUP"] = "1"
        self.assertEqual(self.run_build().returncode, 1)
        self.assertEqual(self.events()[-1][:3], ["docker", "volume", "rm"])

    def test_cleanup_failure_preserves_original_bundle_failure(self):
        self.env.update(FAIL_CLEANUP="1", FAIL_BUNDLE="frontend")
        self.assertEqual(self.run_build().returncode, 19)

    def test_cache_requires_runtime_token_and_results_url(self):
        self.env.update(ACTIONS_RUNTIME_TOKEN="test-token",
                        ACTIONS_RESULTS_URL="https://example.invalid")
        self.assertEqual(self.run_build().returncode, 0)
        builds = [e for e in self.events() if e[:3] == ["docker", "buildx", "build"]]
        self.assertIn("--cache-to", builds[0])
        self.assertFalse(any("--cache-to" in e for e in builds[1:]))
        self.assertFalse(any("test-token" in str(e) for e in self.events()))

    def test_wrong_image_architecture_stops_before_building_bundles(self):
        self.env["IMAGE_ARCH"] = "amd64"
        self.assertNotEqual(self.run_build().returncode, 0)
        self.assertFalse(any(e[0] == "manage" for e in self.events()))

    def test_wrong_image_revision_stops_before_building_bundles(self):
        self.env["IMAGE_SHA"] = "0" * 40
        self.assertNotEqual(self.run_build().returncode, 0)
        self.assertFalse(any(e[0] == "manage" for e in self.events()))


class DockerfileContractTests(unittest.TestCase):
    def test_public_base_stages_are_pinned(self):
        paths = [ROOT / "docker/devenv/Dockerfile"]
        paths.extend(ROOT / f"docker/images/Dockerfile.{role}"
                     for role in ("frontend", "backend", "exporter", "mcp"))
        for path in paths:
            with self.subTest(path=path):
                contents = path.read_text()
                self.assertNotIn("dhi.io/", contents)
                for line in contents.splitlines():
                    if line.startswith("FROM ") and ":" in line:
                        self.assertRegex(line, r"@sha256:[0-9a-f]{64}(?: AS [a-z-]+)?$")

    def test_non_root_runtime_users_are_preserved(self):
        for role in ("frontend", "backend", "exporter", "mcp"):
            with self.subTest(role=role):
                contents = (ROOT / f"docker/images/Dockerfile.{role}").read_text()
                users = [line for line in contents.splitlines() if line.startswith("USER ")]
                self.assertEqual(users[-1], "USER node" if role == "mcp" else "USER penpot:penpot")
                if role != "mcp":
                    self.assertIn("-u 1001", contents)

    def test_access_log_omits_query_string_and_referer(self):
        contents = (ROOT / "docker/images/files/nginx.conf.template").read_text()
        log_format = contents.split("log_format penpot_upstream ", 1)[1].split(";", 1)[0]
        self.assertIn("$request_method $uri $server_protocol", log_format)
        for unsafe in ("$request\"", "$request_uri", "$args", "$query_string", "$http_referer"):
            self.assertNotIn(unsafe, log_format)


if __name__ == "__main__":
    unittest.main()
