"""Durable local job timeline and runtime fingerprints; never send diagnostics."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys


def event(directory, name, **details):
    row = {'time': datetime.now().astimezone().isoformat(), 'pid': os.getpid(),
           'event': name, **details}
    with (Path(directory) / 'events_private.jsonl').open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + '\n')
        handle.flush()
        os.fsync(handle.fileno())


def runtime_manifest():
    package = Path(__file__).resolve().parent
    sources = sorted(package.glob('*.py')) + sorted(package.glob('*.js'))
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    try:
        commit = subprocess.check_output(['git', '-C', str(package.parent), 'rev-parse', 'HEAD'],
                                         stderr=subprocess.DEVNULL, timeout=5).decode().strip()
    except (OSError, subprocess.SubprocessError):
        commit = None
    return {'time': datetime.now().astimezone().isoformat(), 'python': sys.version,
            'platform': platform.platform(), 'pid': os.getpid(), 'git_commit': commit,
            'source_sha256': hashes}


def failure_details(error):
    details = []
    visited = set()
    while error is not None and id(error) not in visited:
        visited.add(id(error))
        item = {'type': type(error).__name__}
        for key in ['command', 'returncode', 'stdout', 'stderr']:
            value = getattr(error, key, None)
            if value is not None:
                item[key] = value.decode('utf-8', 'replace') if isinstance(value, bytes) else value
        details.append(item)
        error = error.__cause__ or error.__context__
    return details
