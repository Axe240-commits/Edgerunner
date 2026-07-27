#!/usr/bin/env python3
"""
One-shot (but kept for reproducibility): re-hash the data_source adapter
hashes of all evidence/*.json artifacts platform-neutrally.

Background: the first evidence artifacts were generated on the Windows host,
whose checkouts use CRLF line endings (and a different commit), so their
adapter hashes match no repo state. This script REPLACES only the code
provenance fields — db hash, coverage, window and results stay untouched
(they are still valid measurements, no research re-run).

Per artifact:
  - adapter/loader hashes recomputed from the REPO files with the
    platform-neutral sha256-text-lf method (see evidence.py)
  - data_source.method set to 'sha256-text-lf'
  - legacy host-based 'git_commit' renamed to 'git_commit_host' (documented
    caveat: on the Windows host it reflects that host's checkout)
  - 'git_commit' set to THIS repo's HEAD
  - audit fields: rehashed_at (epoch), rehash_reason

Idempotent: safe to run repeatedly.
"""
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from evidence import _file_sha256_short, _git_commit, ADAPTER_HASH_METHOD  # noqa: E402

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADAPTER_FILES = ['hyperliquid_api.py', 'history_loader.py']
LOADER_FILES = ['funding_loader.py']
REHASH_REASON = 'platform-neutral adapter hashes'


def rehash_file(path):
    with open(path) as f:
        ev = json.load(f)

    ds = ev.get('data_source', {})
    changed = False

    for name in ADAPTER_FILES:
        repo_file = os.path.join(REPO_DIR, name)
        if name in ds.get('adapter_sha256', {}) and os.path.isfile(repo_file):
            new_hash = _file_sha256_short(repo_file)
            if ds['adapter_sha256'].get(name) != new_hash:
                ds['adapter_sha256'][name] = new_hash
                changed = True
    for name in LOADER_FILES:
        repo_file = os.path.join(REPO_DIR, name)
        if name in ds.get('loader_sha256', {}) and os.path.isfile(repo_file):
            new_hash = _file_sha256_short(repo_file)
            if ds['loader_sha256'].get(name) != new_hash:
                ds['loader_sha256'][name] = new_hash
                changed = True

    ds['method'] = ADAPTER_HASH_METHOD
    ev['data_source'] = ds

    # Host caveat: the old git_commit was the Windows host's checkout.
    if 'git_commit_host' not in ev:
        ev['git_commit_host'] = ev.get('git_commit')
    ev['git_commit'] = _git_commit()

    ev['rehashed_at'] = time.time()
    ev['rehash_reason'] = REHASH_REASON

    with open(path, 'w') as f:
        json.dump(ev, f, indent=1)
    return changed


def main():
    # Only actual evidence artifacts (timestamped *_diagnose/_validate files)
    # — NOT diag reports, which may share the directory.
    paths = sorted(
        glob.glob(os.path.join(REPO_DIR, 'evidence', '2*_diagnose.json'))
        + glob.glob(os.path.join(REPO_DIR, 'evidence', '2*_validate.json')))
    if not paths:
        print('no evidence artifacts found')
        return 1
    for p in paths:
        changed = rehash_file(p)
        print(f'{"rehashed" if changed else "checked "}: {os.path.basename(p)}')
    print(f'done: {len(paths)} artifact(s)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
