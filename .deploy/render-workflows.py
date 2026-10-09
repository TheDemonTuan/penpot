#!/usr/bin/env python3
"""Install fork workflows only after the exact platform commit passes CI."""
import argparse
import os
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


def install(root, checkout, ref, actionlint, replace_platform_ref=None):
    files = render(ref)
    old_files = render(replace_platform_ref) if replace_platform_ref is not None else None
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
    paths = {name: directory / name for name in files}
    existing = {}
    for name, path in paths.items():
        require(not path.is_symlink() and (not path.exists() or path.is_file()),
                'WORKFLOW_EXISTS_WITH_DIFFERENT_CONTENT')
        if path.exists():
            existing[name] = path.read_text(encoding='ascii')
    if old_files is None:
        require(all(existing[name] == files[name] for name in existing),
                'WORKFLOW_EXISTS_WITH_DIFFERENT_CONTENT')
    else:
        require(set(existing) == set(files) and (existing == old_files or existing == files),
                'WORKFLOW_EXISTS_WITH_DIFFERENT_CONTENT')
    directory.mkdir(parents=True, exist_ok=True)
    # Stage both reviewed contents before replacing either active workflow.
    staged = []
    try:
        for name, contents in files.items():
            if existing.get(name) == contents:
                continue
            fd, temporary = tempfile.mkstemp(prefix='.' + name + '.', dir=directory)
            staged.append((Path(temporary), paths[name]))
            with os.fdopen(fd, 'w', encoding='ascii') as stream:
                os.fchmod(stream.fileno(), 0o644)
                stream.write(contents)
                stream.flush()
                os.fsync(stream.fileno())
        for temporary, path in staged:
            os.replace(temporary, path)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)
    return list(files)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--platform-checkout', type=Path, required=True)
    parser.add_argument('--platform-ref', required=True)
    parser.add_argument('--actionlint', default='actionlint')
    parser.add_argument('--replace-platform-ref')
    args = parser.parse_args()
    try:
        names = install(ROOT, args.platform_checkout.resolve(), args.platform_ref, args.actionlint,
                        args.replace_platform_ref)
        print(json.dumps({'status': 'rendered', 'platformRef': args.platform_ref, 'workflows': names}))
    except (RenderFailure, OSError, ValueError, TypeError, KeyError) as error:
        print(json.dumps({'status': 'failed', 'error_code': str(error) if isinstance(error, RenderFailure) else 'WORKFLOW_INPUT_ERROR'}))
        raise SystemExit(1)
