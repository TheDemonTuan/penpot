#!/usr/bin/env python3
"""Smoke source-built images on disposable native ARM64 CI, never on the VPS."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import secrets
import subprocess
import tempfile
import uuid

import yaml

ROLES = ('frontend', 'backend', 'exporter', 'mcp')
STORES = {
    'penpot-postgres': 'postgres@sha256:7e2070cf6ad06fb3cbbd141b1bafbb7fd5bb63e2b6001e6daf34448eb555e4b3',
    'penpot-valkey': 'valkey/valkey@sha256:640c5e62cea04b6d6f2084232651d0cc70362d31f4f805e7be94dbed6855e8f2',
}
SCOPE = re.compile(r'penpot-smoke-[0-9]+-[0-9]+-[0-9a-f]{32}')


class SmokeFailure(Exception):
    pass


def require(condition, code):
    if not condition:
        raise SmokeFailure(code)


def guard(env, architecture):
    require(env.get('GITHUB_ACTIONS') == 'true' and env.get('GITHUB_REPOSITORY') == 'TheDemonTuan/penpot'
            and env.get('GITHUB_REF') == 'refs/heads/main', 'SMOKE_CI_CALLER')
    require(re.fullmatch(r'[0-9a-f]{40}', env.get('GITHUB_SHA', '')) is not None, 'SMOKE_SOURCE_SHA')
    require(all(re.fullmatch(r'[0-9]+', env.get(key, '')) for key in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT')),
            'SMOKE_RUN_ID')
    require(architecture in ('arm64', 'aarch64'), 'SMOKE_NATIVE_ARM64_REQUIRED')
    require(not any(Path(path).exists() for path in ('/etc/vps-deploy/apps', '/opt/platform/edge')),
            'SMOKE_PRODUCTION_HOST_FORBIDDEN')


def compose_fixture(template, sha, scope, master, password):
    require(SCOPE.fullmatch(scope) is not None and re.fullmatch(r'[0-9a-f]{40}', sha) is not None, 'SMOKE_SCOPE')
    value = copy.deepcopy(template)
    expected = {'penpot-' + role for role in ROLES} | set(STORES)
    require(set(value['services']) == expected and set(value['volumes']) == {'penpot_assets', 'penpot_postgres_v15'}
            and set(value['networks']) == {'penpot', 'egress', 'edge'}, 'SMOKE_COMPOSE_CONTRACT')
    for name, service in value['services'].items():
        require(not service.get('ports') and not service.get('network_mode'), 'SMOKE_NO_HOST_PORTS')
        service['container_name'] = scope + '-' + name
        service['restart'] = 'no'
        service.setdefault('labels', {})['penpot.ci.scope'] = scope
        if name in STORES:
            require(service['image'] == STORES[name], 'SMOKE_DATASTORE_DIGEST')
        else:
            role = name.removeprefix('penpot-')
            service['image'] = 'ghcr.io/thedemontuan/penpot-' + role + ':sha-' + sha
        for key, item in service.get('environment', {}).items():
            if key == 'PENPOT_PUBLIC_URI':
                service['environment'][key] = 'http://penpot-frontend:8080'
            elif key == 'PENPOT_SECRET_KEY':
                service['environment'][key] = master
            elif key in ('PENPOT_DATABASE_PASSWORD', 'POSTGRES_PASSWORD'):
                service['environment'][key] = password
    for section in ('networks', 'volumes'):
        for name, resource in value[section].items():
            resource.pop('external', None)
            resource['name'] = scope + '-' + name
            resource.setdefault('labels', {})['penpot.ci.scope'] = scope
    return value


def owned(value, scope):
    require((value.get('Labels') or {}).get('penpot.ci.scope') == scope, 'SMOKE_FOREIGN_RESOURCE')


def run(args, *, data=None, timeout=60, optional=False):
    try:
        result = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise SmokeFailure('SMOKE_COMMAND_FAILED') from None
    if result.returncode:
        if optional:
            return None
        raise SmokeFailure('SMOKE_COMMAND_FAILED')
    return result.stdout


ACCOUNT = r'''
import json, secrets, socket
packet = {'cmd': 'create-profile', 'params': {'fullname': 'Disposable CI', 'email': 'ci-smoke@example.invalid',
                                           'password': secrets.token_urlsafe(32)}}
with socket.create_connection(('127.0.0.1', 6063), timeout=30) as connection:
    stream = connection.makefile(mode='rw')
    stream.write(json.dumps(packet) + '\n')
    stream.flush()
    while True:
        line = stream.readline()
        if not line:
            raise SystemExit(1)
        result = json.loads(line)
        if result.get('tag') == 'ret':
            if result.get('err') or result.get('val', {}).get('email') != packet['params']['email']:
                raise SystemExit(1)
            print('CLI_PROFILE_CREATED')
            break
'''

MCP = r'''
const endpoint='http://penpot-frontend:8080/mcp/stream';
const base={'Content-Type':'application/json','Accept':'application/json, text/event-stream'};
async function call(body, session) {
  const response=await fetch(endpoint,{method:'POST',headers:{...base,...(session?{'mcp-session-id':session}:{})},
      body:JSON.stringify(body),signal:AbortSignal.timeout(15000)});
  if(!response.ok)throw new Error('MCP_HTTP_STATUS');
  if(!('id' in body))return response;
  const text=await response.text();
  const values=response.headers.get('content-type')?.includes('text/event-stream')
    ? text.split(/\r?\n/).filter(line=>line.startsWith('data:')).map(line=>JSON.parse(line.slice(5).trim()))
    : [JSON.parse(text)];
  const result=values.find(value=>value.id===body.id);
  if(!result||result.jsonrpc!=='2.0'||result.error||!result.result)throw new Error('MCP_JSON_RPC');
  return {response,result:result.result};
}
(async()=>{
  const initialized=await call({jsonrpc:'2.0',id:1,method:'initialize',params:{protocolVersion:'2025-03-26',
      capabilities:{},clientInfo:{name:'penpot-ci',version:'1'}}});
  const session=initialized.response.headers.get('mcp-session-id');
  if(!session||!initialized.result.serverInfo)throw new Error('MCP_SESSION');
  await call({jsonrpc:'2.0',method:'notifications/initialized'},session);
  const listed=await call({jsonrpc:'2.0',id:2,method:'tools/list',params:{}},session);
  const names=listed.result.tools?.map(tool=>tool.name)||[];
  if(!names.includes('execute_code')||!names.includes('export_shape'))throw new Error('MCP_TOOLS');
  console.log('MCP_INITIALIZE_TOOLS_OK');
})().catch(()=>process.exit(1));
'''


def persistence(compose, scope):
    require(SCOPE.fullmatch(scope) is not None, 'SMOKE_SCOPE')
    services = ['penpot-' + role for role in ROLES] + list(STORES)

    def identities():
        result = {}
        for name in services:
            obj = json.loads(run(['docker', 'container', 'inspect', scope + '-' + name]))[0]
            owned({'Labels': obj.get('Config', {}).get('Labels') or {}}, scope)
            require(obj.get('State', {}).get('Health', {}).get('Status') == 'healthy', 'SMOKE_STACK_HEALTH')
            require(type(obj.get('Id')) is str and obj['Id'], 'SMOKE_CONTAINER_ID')
            result[name] = obj['Id']
        return result

    before = identities()
    account = ['docker', 'exec', scope + '-penpot-postgres', 'psql', '-X', '-U', 'penpot', '-d', 'penpot',
               '-At', '-c', "SELECT id, is_active FROM profile WHERE email='ci-smoke@example.invalid'"]
    saved = run(account).strip()
    require(re.fullmatch(rb'[0-9a-f-]{36}\|t', saved) is not None, 'SMOKE_ACTIVE_ACCOUNT')
    # A CI-only marker checks the same filesystem volume that stores uploaded assets.
    marker = '/opt/data/assets/' + scope + '.bin'
    asset = ['docker', 'exec', '-i', scope + '-penpot-backend', 'python3', '-c']
    write = 'import hashlib, pathlib, sys; p=pathlib.Path(sys.argv[1]); p.open("xb").write(sys.stdin.buffer.read())'
    run(asset + [write, marker], data=scope.encode('ascii'))
    read = 'import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())'
    checksum = hashlib.sha256(scope.encode('ascii')).hexdigest().encode('ascii')
    require(run(asset + [read, marker]).strip() == checksum, 'SMOKE_ASSET_PERSISTENCE')
    run(compose + ['up', '-d', '--no-deps', '--pull', 'never', '--force-recreate', '--wait',
                   '--wait-timeout', '240', *services[:4]], timeout=600)
    after = identities()
    require(all(before[name] == after[name] for name in STORES), 'SMOKE_DATASTORE_RECREATED')
    require(all(before[name] != after[name] for name in services[:4]), 'SMOKE_APPS_NOT_RECREATED')
    require(run(account).strip() == saved, 'SMOKE_DATABASE_PERSISTENCE')
    require(run(asset + [read, marker]).strip() == checksum, 'SMOKE_ASSET_PERSISTENCE')
    ready = run(['docker', 'exec', scope + '-penpot-frontend', 'curl', '-fsS', '--max-time', '15',
                 '-w', '%{http_code}', 'http://127.0.0.1:8080/readyz'])
    require(ready == b'OK200', 'SMOKE_FRONTEND_READINESS')


def smoke(compose_path):
    guard(os.environ, platform.machine())
    sha = os.environ['GITHUB_SHA']
    require(run(['git', 'rev-parse', 'HEAD']).decode().strip() == sha, 'SMOKE_CHECKOUT_SHA')
    require(run(['docker', 'info', '--format', '{{.Architecture}}']).decode().strip() in ('arm64', 'aarch64'),
            'SMOKE_NATIVE_DAEMON_REQUIRED')
    scope = 'penpot-smoke-' + os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT'] + '-' + uuid.uuid4().hex
    template = yaml.safe_load(compose_path.read_bytes())
    fixture = compose_fixture(template, sha, scope, secrets.token_urlsafe(64), secrets.token_hex(32))
    for role in ROLES:
        ref = fixture['services']['penpot-' + role]['image']
        image = json.loads(run(['docker', 'image', 'inspect', ref]))[0]
        labels = image.get('Config', {}).get('Labels') or {}
        require(image.get('Architecture') == 'arm64' and labels.get('org.opencontainers.image.revision') == sha
                and labels.get('org.opencontainers.image.source') == 'https://github.com/TheDemonTuan/penpot'
                and image.get('Config', {}).get('User', '').split(':')[0] not in ('', '0', 'root'), 'SMOKE_IMAGE_IDENTITY')
    for section, kind in (('services', 'container'), ('volumes', 'volume'), ('networks', 'network')):
        for value in fixture[section].values():
            name = value['container_name' if section == 'services' else 'name']
            require(run(['docker', kind, 'inspect', name], optional=True) is None, 'SMOKE_RESOURCE_COLLISION')
    with tempfile.TemporaryDirectory(prefix='penpot-smoke-') as home:
        path = Path(home) / 'compose.yml'
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w') as stream:
            yaml.safe_dump(fixture, stream, sort_keys=False)
        compose = ['docker', 'compose', '--project-name', scope, '--file', str(path)]
        try:
            run(compose + ['config', '--quiet'])
            run(compose + ['up', '-d', '--wait', '--wait-timeout', '240'], timeout=600)
            for name in fixture['services']:
                obj = json.loads(run(['docker', 'container', 'inspect', scope + '-' + name]))[0]
                owned({'Labels': obj.get('Config', {}).get('Labels') or {}}, scope)
                require(obj.get('State', {}).get('Health', {}).get('Status') == 'healthy'
                        and not obj.get('HostConfig', {}).get('PortBindings'), 'SMOKE_STACK_HEALTH')
            frontend = scope + '-penpot-frontend'
            ready = run(['docker', 'exec', frontend, 'curl', '-fsS', '--max-time', '15', '-w', '%{http_code}',
                         'http://127.0.0.1:8080/readyz'])
            require(ready == b'OK200', 'SMOKE_FRONTEND_READINESS')
            html = run(['docker', 'exec', frontend, 'curl', '-fsS', '--max-time', '15',
                        'http://127.0.0.1:8080/'])
            require(b'<html' in html.lower() and b'<script' in html.lower(), 'SMOKE_FRONTEND_HTML')
            created = run(['docker', 'exec', '-i', scope + '-penpot-backend', 'python3', '-'], data=ACCOUNT.encode(), timeout=60)
            require(created.strip() == b'CLI_PROFILE_CREATED', 'SMOKE_ACCOUNT_CLI')
            active = run(['docker', 'exec', scope + '-penpot-postgres', 'psql', '-X', '-U', 'penpot', '-d', 'penpot',
                          '-At', '-c', "SELECT is_active FROM profile WHERE email='ci-smoke@example.invalid'"])
            require(active.strip() == b't', 'SMOKE_ACTIVE_ACCOUNT')
            persistence(compose, scope)
            protocol = run(['docker', 'exec', '-i', scope + '-penpot-mcp', 'node'], data=MCP.encode(), timeout=60)
            require(protocol.strip() == b'MCP_INITIALIZE_TOOLS_OK', 'SMOKE_MCP_PROTOCOL')
        finally:
            # Never use prune or remove external production volumes.
            run(compose + ['down', '--timeout', '30'], timeout=90)
            for value in fixture['volumes'].values():
                name = value['name']
                inspected = run(['docker', 'volume', 'inspect', name], optional=True)
                if inspected is not None:
                    owned(json.loads(inspected)[0], scope)
                    run(['docker', 'volume', 'rm', name])
    return {'sourceSha': sha, 'platform': 'linux/arm64', 'frontend': True, 'activeAccount': True,
            'mcpInitialize': True, 'mcpToolsList': True, 'appRecreation': True,
            'databasePersistence': True, 'assetPersistence': True, 'datastoresUnchanged': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--compose', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(smoke(args.compose), sort_keys=True))
    except (SmokeFailure, OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({'status': 'failed', 'error_code': str(error) if isinstance(error, SmokeFailure) else 'SMOKE_INPUT_ERROR'}))
        raise SystemExit(1)
