#!/usr/bin/env python3
"""Install fork workflows only after the exact platform commit passes CI."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PLATFORM_REPO = 'TheDemonTuan/vps-deploy'


class RenderFailure(Exception):
    pass


def require(condition, code):
    if not condition:
        raise RenderFailure(code)


def command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        raise RenderFailure('WORKFLOW_VERIFICATION_COMMAND_FAILED') from None
    require(result.returncode == 0, 'WORKFLOW_VERIFICATION_COMMAND_FAILED')
    return result.stdout


def render(ref):
    require(type(ref) is str and re.fullmatch(r'[0-9a-f]{40}', ref) is not None, 'IMMUTABLE_PLATFORM_REF_REQUIRED')
    return {name: (ROOT / '.deploy/workflows' / name).read_text(encoding='ascii').replace('PLATFORM_SHA', ref)
            for name in ('fork-deploy.yml', 'fork-ops.yml')}


def lintable(contents):
    # GitHub documents queue:max, but actionlint does not recognize it yet.
    # Check this one setting ourselves; omit it only from the linter copy.
    expected = 'concurrency:\n  group: penpot-production\n  queue: max\n  cancel-in-progress: false\n'
    require(contents.count(expected) == 1 and contents.count('  queue: max\n') == 1,
            'DEPLOYMENT_QUEUE_POLICY')
    return contents.replace('  queue: max\n', '', 1)


def verify_platform(checkout, ref):
    require(command(['git', '-C', str(checkout), 'rev-parse', 'HEAD']).strip() == ref,
            'PLATFORM_CHECKOUT_REVISION_MISMATCH')
    require(not command(['git', '-C', str(checkout), 'status', '--porcelain=v1', '--untracked-files=all']).strip(),
            'PLATFORM_CHECKOUT_DIRTY')
    for path in ('.github/workflows/build-penpot.yml', 'scripts/validate-penpot-release.py', 'lib/penpot.py'):
        command(['git', '-C', str(checkout), 'cat-file', '-e', ref + ':' + path])
    runs = json.loads(command(['gh', 'api', 'repos/' + PLATFORM_REPO
                              + '/actions/workflows/ci.yml/runs?branch=main&status=success&head_sha=' + ref + '&per_page=100']))
    require(any(run.get('head_sha') == ref and run.get('head_branch') == 'main'
                and run.get('status') == 'completed' and run.get('conclusion') == 'success'
                and (run.get('head_repository') or {}).get('full_name') == PLATFORM_REPO
                for run in runs.get('workflow_runs', [])), 'PLATFORM_CI_REQUIRED')


def install(root, checkout, ref, actionlint):
    files = render(ref)
    verify_platform(checkout, ref)
    require(command(['git', '-C', str(root), 'branch', '--show-current']).strip() == 'main', 'FORK_MAIN_REQUIRED')
    # Validate both files before changing the fork; placeholders never become active workflows.
    with tempfile.TemporaryDirectory(prefix='penpot-workflows-') as home:
        paths = []
        for name, contents in files.items():
            path = Path(home) / name
            path.write_text(lintable(contents), encoding='ascii')
            paths.append(str(path))
        command([actionlint, *paths])
    directory = root / '.github/workflows'
    require(not (root / '.github').is_symlink() and not directory.is_symlink(), 'WORKFLOW_PATH_SYMLINK')
    for name, contents in files.items():
        path = directory / name
        require(not path.is_symlink() and (not path.exists() or path.read_text(encoding='ascii') == contents),
                'WORKFLOW_EXISTS_WITH_DIFFERENT_CONTENT')
    directory.mkdir(parents=True, exist_ok=True)
    for name, contents in files.items():
        path = directory / name
        if not path.exists():
            with path.open('x', encoding='ascii') as stream:
                stream.write(contents)
    return list(files)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--platform-checkout', type=Path, required=True)
    parser.add_argument('--platform-ref', required=True)
    parser.add_argument('--actionlint', default='actionlint')
    args = parser.parse_args()
    try:
        names = install(ROOT, args.platform_checkout.resolve(), args.platform_ref, args.actionlint)
        print(json.dumps({'status': 'rendered', 'platformRef': args.platform_ref, 'workflows': names}))
    except (RenderFailure, OSError, ValueError, TypeError, KeyError) as error:
        print(json.dumps({'status': 'failed', 'error_code': str(error) if isinstance(error, RenderFailure) else 'WORKFLOW_INPUT_ERROR'}))
        raise SystemExit(1)
