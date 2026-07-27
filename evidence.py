#!/usr/bin/env python3
"""
Evidence artifacts for research runs.

Every diagnose/validate run of the backtest scripts writes, next to its JSON
report, an immutable evidence file:
  evidence/<YYYYMMDD-HHMMSS>_<script>_<mode>.json   (relative to the repo)

The artifact pins down WHAT produced the result: exact command, code version
(git commit + adapter/loader file hashes), and WHICH data it ran on
(DB path + sha256, per-table coverage incl. gap report). Committed to the
repo, so every number in RESEARCH_FINDINGS.md is reproducible.

DB hashing rule (documented): full sha256 when the file is <= FULL_HASH_MAX_BYTES
(streamed in 8 MB chunks, well under 60 s on SSD); otherwise a partial hash
(first + last 64 MB) with the reason recorded. Row counts are always included.
"""
import hashlib
import json
import os
import sqlite3
import time

EVIDENCE_VERSION = 1
FULL_HASH_MAX_BYTES = 2 * 1024 ** 3   # full sha256 below ~2 GB, else partial
PARTIAL_CHUNK = 64 * 1024 ** 2        # first + last 64 MB for partial hashes
HASH_CHUNK = 8 * 1024 ** 2

# candle interval per timeframe (kept in sync with hyperliquid_api.TF_MS;
# duplicated here to keep evidence.py importable without the API module)
TF_MS = {
    '1m': 60_000, '3m': 180_000, '5m': 300_000, '10m': 600_000,
    '15m': 900_000, '30m': 1_800_000, '1h': 3_600_000, '2h': 7_200_000,
    '4h': 14_400_000, '1d': 86_400_000,
}


def _sha256_file(path):
    """Streamed sha256 of a file (full or partial per the rule above)."""
    size = os.path.getsize(path)
    started = time.time()
    h = hashlib.sha256()
    if size <= FULL_HASH_MAX_BYTES:
        method = 'full'
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(HASH_CHUNK), b''):
                h.update(chunk)
    else:
        method = 'partial-first-last-64MB'
        with open(path, 'rb') as f:
            h.update(f.read(PARTIAL_CHUNK))
            f.seek(max(0, size - PARTIAL_CHUNK))
            h.update(f.read(PARTIAL_CHUNK))
    return {
        'sha256': h.hexdigest(),
        'method': method,
        'size_bytes': size,
        'hash_seconds': round(time.time() - started, 2),
    }


def _file_sha256_short(path, n=16):
    """Short sha256 (first n hex chars) of a source file — adapter version."""
    try:
        h = hashlib.sha256()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(HASH_CHUNK), b''):
                h.update(chunk)
        return h.hexdigest()[:n]
    except OSError:
        return None


def _git_commit():
    import subprocess
    try:
        r = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True,
                           text=True, timeout=5)
        return r.stdout.strip() or 'unknown'
    except Exception:
        return 'unknown'


def _coverage(conn, table, interval_ms):
    """Row count, timestamp span and gap report (>2x interval) per table."""
    rows = conn.execute(f'SELECT COUNT(*), MIN(timestamp), MAX(timestamp) '
                        f'FROM {table}').fetchone()
    gap_row = conn.execute(
        f'SELECT COUNT(*), MAX(gap) FROM ('
        f'  SELECT timestamp - LAG(timestamp) OVER (ORDER BY timestamp) AS gap'
        f'  FROM {table}) WHERE gap > ?', (2 * interval_ms,)).fetchone()
    biggest = gap_row[1]
    return {
        'rows': rows[0],
        'min_ts': rows[1],
        'max_ts': rows[2],
        'gaps_over_2x_interval': gap_row[0],
        'biggest_gap_intervals': (round(biggest / interval_ms, 1)
                                  if biggest else 0),
    }


def build_evidence(script, mode, argv, cfg, db_path, result_path,
                   tables, adapter_name, adapter_files, loader_files=None,
                   window=None, train_cutoff_ts=None, out_dir=None):
    """Build and write the evidence artifact. Returns its path.

    tables: list of candle table names used (e.g. ['candles_4h', 'candles_1h']).
    adapter_files: source files hashed as the adapter version.
    loader_files: extra loaders hashed (e.g. funding_loader.py).
    """
    repo_dir = os.path.dirname(os.path.abspath(__file__))
    out_dir = out_dir or os.path.join(repo_dir, 'evidence')
    os.makedirs(out_dir, exist_ok=True)

    db_info = {'path': db_path}
    db_info.update(_sha256_file(db_path))

    conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    try:
        coverage = {}
        for t in tables:
            tf = t.replace('candles_', '')
            ims = TF_MS.get(tf, 60_000)
            coverage[t] = _coverage(conn, t, ims)
    finally:
        conn.close()

    result_sha256 = None
    if result_path and os.path.isfile(result_path):
        result_sha256 = _sha256_file(result_path)['sha256']

    data_source = {
        'name': adapter_name,
        'adapter_sha256': {os.path.basename(f): _file_sha256_short(f)
                           for f in adapter_files},
    }
    if loader_files:
        data_source['loader_sha256'] = {os.path.basename(f): _file_sha256_short(f)
                                        for f in loader_files}

    train_fraction = getattr(cfg, 'train_fraction', None)
    evidence = {
        'evidence_version': EVIDENCE_VERSION,
        'script': script,
        'mode': mode,
        'argv': list(argv),
        'git_commit': _git_commit(),
        'data_source': data_source,
        'db': db_info,
        'window': {
            'since': (window or {}).get('since'),
            'until': (window or {}).get('until'),
            'train_fraction': train_fraction,
            'train_cutoff_ts': train_cutoff_ts,
        },
        'coverage': coverage,
        'result_file': result_path,
        'result_sha256': result_sha256,
    }

    stamp = time.strftime('%Y%m%d-%H%M%S', time.gmtime())
    base = os.path.basename(script).replace('.py', '')
    out_path = os.path.join(out_dir, f'{stamp}_{base}_{mode}.json')
    with open(out_path, 'w') as f:
        json.dump(evidence, f, indent=1)
    return out_path
