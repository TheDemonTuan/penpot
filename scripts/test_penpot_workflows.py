import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]


class PenpotWorkflowRendering(unittest.TestCase):
    def module(self):
        spec = importlib.util.spec_from_file_location('render_penpot_workflows', ROOT / '.deploy/render-workflows.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_no_mutable_or_missing_platform_revision_can_render(self):
        renderer = self.module()
        for value in ('main', 'latest', '', 'a' * 39, 'A' * 40):
            with self.subTest(value=value), self.assertRaises(renderer.RenderFailure):
                renderer.render(value)

    def test_deploy_graph_requires_source_build_and_every_role_scan(self):
        renderer = self.module()
        sha = 'a' * 40
        files = renderer.render(sha)
        workflow = yaml.safe_load(files['fork-deploy.yml'])
        jobs = workflow['jobs']
        self.assertEqual(jobs['source-scan']['needs'], 'fence')
        self.assertEqual(jobs['build']['needs'], 'source-scan')
        self.assertEqual(set(jobs['deploy']['needs']), {'build', 'scan-frontend', 'scan-backend', 'scan-exporter', 'scan-mcp'})
        self.assertEqual(workflow['concurrency']['cancel-in-progress'], False)
        self.assertEqual(workflow['concurrency']['queue'], 'max')
        for role in ('frontend', 'backend', 'exporter', 'mcp'):
            job = jobs['scan-' + role]
            self.assertEqual(job['needs'], 'build')
            self.assertEqual(job['with']['image-role'], role)
            self.assertEqual(job['with']['vuln-severity'], 'HIGH,CRITICAL')
            self.assertEqual(job['with']['platform-ref'], sha)
            self.assertTrue(job['uses'].endswith('@' + sha))
        self.assertEqual(jobs['deploy']['environment'], 'production')
        self.assertIn("PENPOT_DEPLOY_ENABLED == 'true'", jobs['deploy']['if'])
        self.assertNotIn('PLATFORM_SHA', files['fork-deploy.yml'])
        download = jobs['deploy']['steps'][0]['with']
        self.assertIn('release-artifact-id', download['artifact-ids'])
        self.assertNotIn('run-id', download)

    def test_linter_copy_only_omits_the_exact_documented_queue_setting(self):
        renderer = self.module()
        text = renderer.render('a' * 40)['fork-deploy.yml']
        checked = renderer.lintable(text)
        self.assertEqual(checked, text.replace('  queue: max\n', '', 1))
        self.assertIn('cancel-in-progress: false', checked)
        self.assertIn('artifact-ids:', checked)
        for invalid in (text.replace('queue: max', 'queue: single'),
                        text.replace('cancel-in-progress: false', 'cancel-in-progress: true'),
                        text.replace('group: penpot-production', 'group: other')):
            with self.assertRaisesRegex(renderer.RenderFailure, 'DEPLOYMENT_QUEUE_POLICY'):
                renderer.lintable(invalid)

    def test_ops_share_deploy_lock_and_cannot_request_image_rollback(self):
        files = self.module().render('b' * 40)
        deploy = yaml.safe_load(files['fork-deploy.yml'])
        ops = yaml.safe_load(files['fork-ops.yml'])
        self.assertEqual(deploy['concurrency'], ops['concurrency'])
        trigger = ops.get('on', ops.get(True))
        self.assertEqual(trigger['workflow_dispatch']['inputs']['operation']['options'], ['status', 'reconcile'])
        self.assertIn('refs/heads/main', ops['jobs']['operation']['if'])

    def test_dirty_platform_checkout_cannot_consume_ci_evidence(self):
        renderer = self.module()
        with mock.patch.object(renderer, 'command', side_effect=['a' * 40, ' M lib/penpot.py']) as command:
            with self.assertRaisesRegex(renderer.RenderFailure, 'PLATFORM_CHECKOUT_DIRTY'):
                renderer.verify_platform(Path('/tmp/platform'), 'a' * 40)
        self.assertEqual(command.call_count, 2)

    def test_platform_ci_evidence_requires_exact_successful_main_revision(self):
        renderer = self.module()
        sha = 'a' * 40
        run = {'head_sha': sha, 'head_branch': 'main', 'status': 'completed', 'conclusion': 'success',
               'head_repository': {'full_name': 'TheDemonTuan/vps-deploy'}}
        import copy
        import json
        def check(value):
            with mock.patch.object(renderer, 'command', side_effect=[sha, '', '', '', '', json.dumps({'workflow_runs': [value]})]):
                renderer.verify_platform(Path('/tmp/platform'), sha)
        check(run)
        for key, wrong in [('head_sha', 'b' * 40), ('head_branch', 'develop'), ('status', 'in_progress'),
                           ('conclusion', 'failure'), ('head_repository', {'full_name': 'foreign/platform'})]:
            value = copy.deepcopy(run)
            value[key] = wrong
            with self.subTest(key=key), self.assertRaisesRegex(renderer.RenderFailure, 'PLATFORM_CI_REQUIRED'):
                check(value)

    def test_failed_platform_verification_never_writes_fork_workflows(self):
        renderer = self.module()
        with tempfile.TemporaryDirectory() as home:
            root = Path(home)
            with mock.patch.object(renderer, 'verify_platform', side_effect=renderer.RenderFailure('PLATFORM_CI_REQUIRED')):
                with self.assertRaises(renderer.RenderFailure):
                    renderer.install(root, Path(home) / 'platform', 'c' * 40, 'actionlint')
            self.assertFalse((root / '.github').exists())


if __name__ == '__main__':
    unittest.main()
