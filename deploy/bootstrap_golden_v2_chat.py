"""Prepare only the independent non-paid V2 chat runtime and private client.

Run as the host operator after placing the reviewed exact release below
/opt/course-transcript-v2-chat/releases/<sha>. No credentials are printed,
no V1 state is touched and no provider/publication authorization is created.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import stat

ROOT = Path('/opt/course-transcript-v2-chat')


def private_write(path: Path, text: str, *, uid: int, gid: int) -> None:
    if path.is_symlink(): raise ValueError('private configuration cannot be a symlink')
    if path.exists():
        info = path.stat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid
                or stat.S_IMODE(info.st_mode) & 0o077 or path.read_text() != text):
            raise ValueError('existing private configuration needs reconciliation')
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(text); stream.flush(); os.fsync(stream.fileno())
    os.chown(path, uid, gid)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-sha', required=True)
    parser.add_argument('--client-directory', type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0 or not re.fullmatch(r'[0-9a-f]{40}', args.release_sha):
        parser.error('host operator and reviewed exact release SHA required')
    if ROOT.is_symlink(): parser.error('runtime root cannot be a symlink')
    release = ROOT / 'releases' / args.release_sha
    if release.is_symlink() or not (release / 'scripts/golden_v2_chat_client.py').is_file():
        parser.error('reviewed release is not present')
    directory = args.client_directory
    if (not directory.is_absolute() or directory.is_symlink()
            or not str(directory).startswith('/srv/ai-workspace/projects/')
            or directory.name != 'golden-v2-chat'):
        parser.error('the intended Runner project chat directory is required')
    owner = pwd.getpwnam('webcodex')
    ROOT.mkdir(parents=True, exist_ok=True); ROOT.chmod(0o755)
    secret_dir = ROOT / 'secrets'; data = ROOT / 'data'
    for path in [secret_dir, data]:
        if path.is_symlink(): parser.error('runtime state cannot be a symlink')
        path.mkdir(mode=0o700, exist_ok=True); path.chmod(0o700)
    api_env = secret_dir / 'api.env'
    if api_env.exists():
        if api_env.is_symlink() or stat.S_IMODE(api_env.stat().st_mode) & 0o077:
            parser.error('existing API configuration is not private')
        values = dict(line.split('=', 1) for line in api_env.read_text().splitlines() if line)
        read = values.get('COURSE_TRANSCRIPT_V2_STATUS_TOKEN', '')
        write = values.get('COURSE_TRANSCRIPT_V2_WRITE_TOKEN', '')
        if min(len(read), len(write)) < 32: parser.error('existing scoped tokens require reconciliation')
    else:
        read, write = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
        text = ('COURSE_TRANSCRIPT_V2_STATUS_API_ENABLED=true\n'
                'COURSE_TRANSCRIPT_V2_WRITE_API_ENABLED=true\n'
                'COURSE_TRANSCRIPT_V2_OWNER_ACTOR=chatgpt-webcodex-owner\n'
                'COURSE_TRANSCRIPT_V2_MAX_APPROVAL_CENTS=1000\n'
                'COURSE_TRANSCRIPT_V2_STATUS_TOKEN=' + read + '\n'
                'COURSE_TRANSCRIPT_V2_WRITE_TOKEN=' + write + '\n')
        private_write(api_env, text, uid=0, gid=0)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700); os.chown(directory, owner.pw_uid, owner.pw_gid)
    client_text = ('COURSE_TRANSCRIPT_V2_OWNER_API_URL=http://127.0.0.1:8816\n'
                   'COURSE_TRANSCRIPT_V2_STATUS_TOKEN=' + read + '\n'
                   'COURSE_TRANSCRIPT_V2_WRITE_TOKEN=' + write + '\n')
    private_write(directory / 'private-client.env', client_text, uid=owner.pw_uid, gid=owner.pw_gid)
    entry = '''from pathlib import Path
import json,re,sys
base=Path(__file__).resolve().parent
runtime=json.loads((base/'runtime.json').read_text())
sha=runtime['release_sha']
if not re.fullmatch(r'[0-9a-f]{40}',sha): raise SystemExit('invalid V2 release binding')
release=Path('/opt/course-transcript-v2-chat/releases')/sha
sys.path.insert(0,str(release))
from scripts.golden_v2_chat_client import main
sys.argv=[sys.argv[0],'--config',str(base/'private-client.env'),*sys.argv[1:]]
raise SystemExit(main())
'''
    for name, text in [('entry.py', entry), ('runtime.json', json.dumps({'release_sha': args.release_sha}, sort_keys=True)+'\n'),
                       ('.gitignore', 'private-client.env\n')]:
        path = directory / name
        if path.is_symlink(): parser.error('chat bridge file cannot be a symlink')
        path.write_text(text); path.chmod(0o600); os.chown(path, owner.pw_uid, owner.pw_gid)
    print(json.dumps({'action':'PRIVATE_CHAT_RUNTIME_PREPARED','release_sha':args.release_sha,
                      'tokens_printed':False,'paid_executor_configured':False,'v1_modified':False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
