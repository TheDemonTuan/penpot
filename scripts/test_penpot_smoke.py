import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('penpot_smoke', ROOT / '.deploy/smoke-stack.py')
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)


class SmokeStackContract(unittest.TestCase):
    def setUp(self):
        self.env = {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': 'TheDemonTuan/penpot',
                    'GITHUB_REF': 'refs/heads/main', 'GITHUB_SHA': 'a' * 40,
                    'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1'}
        self.template = yaml.safe_load((ROOT.parent / 'vps-deploy/apps/penpot/docker-compose.prod.yml').read_text())
        self.scope = 'penpot-smoke-123-1-' + 'b' * 32

    def test_guards_reject_non_ci_foreign_mutable_and_non_native_callers(self):
        SMOKE.guard(self.env, 'aarch64')
        for name, value in [('GITHUB_ACTIONS', 'false'), ('GITHUB_REPOSITORY', 'foreign/penpot'),
                            ('GITHUB_REF', 'refs/heads/develop'), ('GITHUB_SHA', 'main'),
                            ('GITHUB_RUN_ID', '../prod'), ('GITHUB_RUN_ATTEMPT', '')]:
            env = dict(self.env, **{name: value})
            with self.subTest(name=name), self.assertRaises(SMOKE.SmokeFailure):
                SMOKE.guard(env, 'aarch64')
        with self.assertRaises(SMOKE.SmokeFailure):
            SMOKE.guard(self.env, 'x86_64')

    def test_smoke_refuses_an_installed_production_host_even_with_ci_env(self):
        with mock.patch.object(SMOKE.Path, 'exists', return_value=True):
            with self.assertRaisesRegex(SMOKE.SmokeFailure, 'SMOKE_PRODUCTION_HOST_FORBIDDEN'):
                SMOKE.guard(self.env, 'aarch64')

    def test_fixture_is_zero_port_and_never_reuses_production_resources(self):
        original = copy.deepcopy(self.template)
        value = SMOKE.compose_fixture(self.template, 'a' * 40, self.scope, 'master', 'db')
        self.assertEqual(self.template, original)
        self.assertEqual(len(value['services']), 6)
        for name, service in value['services'].items():
            self.assertEqual(service['container_name'], self.scope + '-' + name)
            self.assertNotIn('ports', service)
            self.assertEqual(service['labels']['penpot.ci.scope'], self.scope)
            if name in ('penpot-frontend', 'penpot-backend', 'penpot-exporter'):
                self.assertEqual(service['environment']['PENPOT_PUBLIC_URI'], 'http://penpot-frontend:8080')
        for section in ('volumes', 'networks'):
            for name, resource in value[section].items():
                self.assertFalse(resource.get('external', False))
                self.assertEqual(resource['name'], self.scope + '-' + name)
                self.assertEqual(resource['labels']['penpot.ci.scope'], self.scope)
        for role in SMOKE.ROLES:
            self.assertEqual(value['services']['penpot-' + role]['image'],
                             'ghcr.io/thedemontuan/penpot-' + role + ':sha-' + 'a' * 40)
        self.assertNotIn('design.tuannguyenviet.site', json.dumps(value))

    def test_template_cannot_add_services_ports_or_change_datastore_refs(self):
        for fault in ('extra', 'port', 'datastore', 'scope'):
            value = copy.deepcopy(self.template)
            scope = self.scope
            if fault == 'extra':
                value['services']['foreign'] = {}
            elif fault == 'port':
                value['services']['penpot-backend']['ports'] = ['6060:6060']
            elif fault == 'datastore':
                value['services']['penpot-postgres']['image'] = 'postgres:latest'
            else:
                scope = 'penpot'
            with self.subTest(fault=fault), self.assertRaises(SMOKE.SmokeFailure):
                SMOKE.compose_fixture(value, 'a' * 40, scope, 'master', 'db')

    def test_mcp_probe_accepts_json_or_sse_and_rejects_errors_or_missing_tools(self):
        for mode in ('json', 'sse', 'error', 'missing-tools', 'missing-session'):
            prefix = r'''
const mode=MODE;
global.fetch=async(url,options)=>{
  if(url!=='http://penpot-frontend:8080/mcp/stream')throw Error('wrong ingress');
  const request=JSON.parse(options.body);
  if(request.method!=='initialize'&&options.headers['mcp-session-id']!=='test-session')throw Error('unbound session');
  const result=request.method==='initialize'?{serverInfo:{name:'penpot'}}:
     {tools:mode==='missing-tools'?[]:[{name:'execute_code'},{name:'export_shape'}]};
  const value=mode==='error'?{jsonrpc:'2.0',id:request.id,error:{code:-1}}:{jsonrpc:'2.0',id:request.id,result};
  return {ok:true,headers:{get:(name)=>name==='mcp-session-id'?(mode==='missing-session'?null:'test-session'):
     mode==='sse'?'text/event-stream':'application/json'},text:async()=>mode==='sse'?'data: '+JSON.stringify(value)+'\n\n':JSON.stringify(value)};
};
'''.replace('MODE', json.dumps(mode))
            with self.subTest(mode=mode):
                result = subprocess.run(['node'], input=prefix + SMOKE.MCP, text=True, capture_output=True, timeout=20)
                if mode in ('json', 'sse'):
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.strip(), 'MCP_INITIALIZE_TOOLS_OK')
                else:
                    self.assertEqual(result.returncode, 1)
                    self.assertNotIn('MCP_INITIALIZE_TOOLS_OK', result.stdout)

    def test_embedded_account_program_has_valid_python_syntax(self):
        compile(SMOKE.ACCOUNT, '<disposable-profile>', 'exec')

    def test_persistence_recreates_only_apps_and_preserves_data(self):
        calls = []
        inspected = {name: 0 for name in self.template['services']}

        def runner(args, **kwargs):
            calls.append((args, kwargs))
            if args[:3] == ['docker', 'container', 'inspect']:
                name = args[3].removeprefix(self.scope + '-')
                inspected[name] += 1
                identity = name + ('-new' if name not in SMOKE.STORES and inspected[name] > 1 else '-old')
                return json.dumps([{'Id': identity, 'Config': {'Labels': {'penpot.ci.scope': self.scope}},
                                    'State': {'Health': {'Status': 'healthy'}}}]).encode()
            if 'psql' in args:
                return b'11111111-1111-1111-1111-111111111111|t\n'
            if 'python3' in args:
                return hashlib.sha256(self.scope.encode()).hexdigest().encode() + b'\n'
            if 'curl' in args:
                return b'OK200'
            return b''

        compose = ['docker', 'compose', '--project-name', self.scope]
        with mock.patch.object(SMOKE, 'run', side_effect=runner):
            SMOKE.persistence(compose, self.scope)
        recreated = next(args for args, _ in calls if '--force-recreate' in args)
        self.assertIn('--no-deps', recreated)
        self.assertIn('--pull', recreated)
        self.assertEqual(recreated[-4:], ['penpot-' + role for role in SMOKE.ROLES])
        self.assertNotIn('penpot-postgres', recreated)
        self.assertNotIn('penpot-valkey', recreated)

    def test_persistence_rejects_data_loss_datastore_recreation_or_app_reuse(self):
        for fault in ('database', 'asset', 'datastore', 'app', 'foreign', 'unhealthy'):
            counts = {}

            def runner(args, **kwargs):
                if args[:3] == ['docker', 'container', 'inspect']:
                    name = args[3].removeprefix(self.scope + '-')
                    counts[name] = counts.get(name, 0) + 1
                    second = counts[name] > 1
                    replaced = second and (name not in SMOKE.STORES or fault == 'datastore')
                    if fault == 'app' and name not in SMOKE.STORES:
                        replaced = False
                    labels = {'penpot.ci.scope': 'foreign' if fault == 'foreign' else self.scope}
                    health = 'unhealthy' if fault == 'unhealthy' and second else 'healthy'
                    return json.dumps([{'Id': name + ('-new' if replaced else '-old'),
                                        'Config': {'Labels': labels},
                                        'State': {'Health': {'Status': health}}}]).encode()
                if 'python3' in args and 'p.open("xb")' in args[-2]:
                    return b''
                key = 'database' if 'psql' in args else 'asset' if 'python3' in args else None
                if key:
                    counts[key] = counts.get(key, 0) + 1
                    if fault == key and counts[key] > 1:
                        return b''
                    return (b'11111111-1111-1111-1111-111111111111|t\n' if key == 'database'
                            else hashlib.sha256(self.scope.encode()).hexdigest().encode() + b'\n')
                return b'OK200' if 'curl' in args else b''

            with self.subTest(fault=fault), mock.patch.object(SMOKE, 'run', side_effect=runner):
                with self.assertRaises(SMOKE.SmokeFailure):
                    SMOKE.persistence(['docker', 'compose'], self.scope)

    def test_cleanup_refuses_foreign_labels(self):
        SMOKE.owned({'Labels': {'penpot.ci.scope': self.scope}}, self.scope)
        for labels in ({}, {'penpot.ci.scope': 'penpot'}, {'penpot.ci.scope': self.scope + '-other'}):
            with self.assertRaises(SMOKE.SmokeFailure):
                SMOKE.owned({'Labels': labels}, self.scope)


if __name__ == '__main__':
    unittest.main()
