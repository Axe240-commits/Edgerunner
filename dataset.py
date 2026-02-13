#!/usr/bin/env python3
"""
Edgerunner Dataset — PyTorch Dataset for training.

Loads features from SQLite, normalizes, and provides train/test splits.
Supports multi-TF via timeframe parameter.
"""
import sqlite3
import math
import numpy as np

try:
    import torch
    from torch.utils.data import Dataset
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False
    class Dataset:
        pass

from db import DEFAULT_DB_PATH, NUMERIC_FEATURES, _connect, _table_name


# Feature categories for normalization strategy
BOOLEAN_FEATURES = {
    'is_bullish', 'is_swing_high', 'is_swing_low', 'bos_bull', 'bos_bear',
    'choch', 'bos_body', 'bos_wick', 'sw_bullish', 'same_dir',
    'broken_was_seeker', 'broken_was_seeker_div', 'swing_had_break',
    'macd_peak', 'macd_trough', 'bull_div', 'bear_div', 'div_near_daily',
    'is_seeker_hs', 'is_seeker_ls', 'is_seeker_div', 'is_seeker_kill',
    'candle_was_seeker', 'candle_was_seeker_div',
    'whale_cluster', 'elite_whale_active', 'htf_bos',
}

BOUNDED_FEATURES = {
    'rsi14': (0, 100),
    'body_ratio': (0, 1),
    'body_position': (0, 1),
    'whale_sentiment': (-1, 1),
    'whale_confidence': (0, 1),
    'bull_pressure': (0, 1),
    'bear_pressure': (0, 1),
    'whale_cluster_strength': (0, 1),
    'div_strength': (0, 1),
}

LOG_FEATURES = {'volume', 'atr14'}

# Whale features that may be NULL
WHALE_FEATURES = {
    'whale_sentiment', 'whale_confidence', 'bull_pressure', 'bear_pressure',
    'whale_cluster', 'whale_cluster_strength', 'whale_cluster_dir',
    'elite_whale_active',
}

# Target columns from outcomes table
TARGET_OPTIONS = {
    'pnl_1m': 'outcomes.pnl_1m',
    'pnl_5m': 'outcomes.pnl_5m',
    'pnl_15m': 'outcomes.pnl_15m',
    'pnl_1h': 'outcomes.pnl_1h',
    'max_favorable': 'outcomes.max_favorable',
    'max_adverse': 'outcomes.max_adverse',
    'direction_5m': 'CASE WHEN outcomes.pnl_5m > 0 THEN 1 ELSE 0 END',
    'profitable_5m': 'CASE WHEN outcomes.pnl_5m > 0.1 THEN 1 ELSE 0 END',
}


class EdgerunnerDataset(Dataset):
    """PyTorch Dataset for Edgerunner candle features.

    Args:
        db_path: Path to SQLite database
        timeframe: Which TF table to load from (default '1m')
        scenario: Optional scenario name (only use those candles)
        window_size: 1 = single candle, N = sequence of N candles
        target: What to predict (pnl_5m, direction_5m, max_favorable, etc.)
        target_binary: True = classification (up/down), False = regression
        normalize: Apply normalization
        train_split: Fraction for training (default 0.8)
        split: "train" or "test"
    """

    # Features used (excludes non-numeric)
    FEATURE_COLS = [f for f in NUMERIC_FEATURES if f not in ('htf_swing_high', 'htf_swing_low')]

    def __init__(self, db_path=DEFAULT_DB_PATH, timeframe='1m', scenario=None,
                 window_size=1, target='pnl_5m', target_binary=True,
                 normalize=True, train_split=0.8, split='train'):
        if not HAS_TORCH:
            raise ImportError('PyTorch is required: pip install torch')

        self.db_path = db_path
        self.timeframe = timeframe
        self.window_size = window_size
        self.target_key = target
        self.target_binary = target_binary
        self.normalize = normalize
        self.split = split

        # Load data
        features, targets, has_whale = self._load_data(scenario, target)

        if len(features) == 0:
            self.features = torch.empty(0)
            self.targets = torch.empty(0)
            self._stats = {}
            return

        # Add has_whale_data flag
        self.FEATURE_COLS_ACTUAL = list(self.FEATURE_COLS) + ['has_whale_data']
        features = np.column_stack([features, has_whale.astype(np.float32)])

        # Compute stats on full data before split
        self._stats = self._compute_stats(features)

        # Normalize
        if normalize:
            features = self._normalize(features)

        # Split
        split_idx = int(len(features) * train_split)
        if split == 'train':
            features = features[:split_idx]
            targets = targets[:split_idx]
        else:
            features = features[split_idx:]
            targets = targets[split_idx:]

        # Convert to tensors
        self.features = torch.tensor(features, dtype=torch.float32)
        if target_binary:
            self.targets = torch.tensor(targets, dtype=torch.long)
        else:
            self.targets = torch.tensor(targets, dtype=torch.float32)

    def __len__(self):
        if self.window_size <= 1:
            return len(self.features)
        return max(0, len(self.features) - self.window_size + 1)

    def __getitem__(self, idx):
        if self.window_size <= 1:
            return self.features[idx], self.targets[idx]
        end = idx + self.window_size
        return self.features[idx:end], self.targets[end - 1]

    def get_feature_names(self):
        return list(getattr(self, 'FEATURE_COLS_ACTUAL', self.FEATURE_COLS))

    def get_stats(self):
        return self._stats

    def get_num_features(self):
        return len(getattr(self, 'FEATURE_COLS_ACTUAL', self.FEATURE_COLS))

    # =========================================================================
    # DATA LOADING
    # =========================================================================

    def _load_data(self, scenario, target):
        """Load features and targets from DB. Returns (features_np, targets_np, has_whale_np)."""
        table = _table_name(self.timeframe)
        conn = _connect(self.db_path)

        target_sql = TARGET_OPTIONS.get(target, f'outcomes.{target}')
        cols_sql = ', '.join(f'c.{col}' for col in self.FEATURE_COLS)

        if scenario:
            sql = f'''
                SELECT {cols_sql}, {target_sql} as target
                FROM scenario_candles sc
                JOIN {table} c ON sc.candle_id = c.id
                JOIN outcomes ON c.id = outcomes.candle_id AND outcomes.timeframe = ?
                WHERE sc.scenario_id = (SELECT id FROM scenarios WHERE name = ?)
                AND sc.timeframe = ?
                AND {target_sql} IS NOT NULL
                ORDER BY c.timestamp
            '''
            rows = conn.execute(sql, (self.timeframe, scenario, self.timeframe)).fetchall()
        else:
            sql = f'''
                SELECT {cols_sql}, {target_sql} as target
                FROM {table} c
                JOIN outcomes ON c.id = outcomes.candle_id AND outcomes.timeframe = ?
                WHERE {target_sql} IS NOT NULL
                ORDER BY c.timestamp
            '''
            rows = conn.execute(sql, (self.timeframe,)).fetchall()

        conn.close()

        if not rows:
            return np.empty((0, len(self.FEATURE_COLS))), np.empty(0), np.empty(0)

        n = len(rows)
        n_features = len(self.FEATURE_COLS)
        features = np.zeros((n, n_features), dtype=np.float32)
        targets = np.zeros(n, dtype=np.float32)
        has_whale = np.zeros(n, dtype=np.float32)

        for i, row in enumerate(rows):
            for j, col in enumerate(self.FEATURE_COLS):
                val = row[j]
                if val is None:
                    features[i, j] = 0.0
                    if col in WHALE_FEATURES:
                        pass
                else:
                    features[i, j] = float(val)
                    if col in WHALE_FEATURES:
                        has_whale[i] = 1.0

            target_val = row[n_features]
            if self.target_binary:
                targets[i] = 1.0 if target_val > 0 else 0.0
            else:
                targets[i] = float(target_val)

        return features, targets, has_whale

    # =========================================================================
    # NORMALIZATION
    # =========================================================================

    def _compute_stats(self, features):
        stats = {}
        col_names = getattr(self, 'FEATURE_COLS_ACTUAL', self.FEATURE_COLS)
        for j, col in enumerate(col_names):
            vals = features[:, j]
            stats[col] = {
                'mean': float(np.nanmean(vals)),
                'std': float(np.nanstd(vals)),
                'min': float(np.nanmin(vals)),
                'max': float(np.nanmax(vals)),
            }
        return stats

    def _normalize(self, features):
        features = features.copy()
        col_names = getattr(self, 'FEATURE_COLS_ACTUAL', self.FEATURE_COLS)

        for j, col in enumerate(col_names):
            if col in BOOLEAN_FEATURES or col == 'has_whale_data':
                continue

            if col in BOUNDED_FEATURES:
                lo, hi = BOUNDED_FEATURES[col]
                rng = hi - lo
                if rng > 0:
                    features[:, j] = (features[:, j] - lo) / rng
                continue

            if col in LOG_FEATURES:
                features[:, j] = np.log1p(np.abs(features[:, j]))

            s = self._stats.get(col, {})
            mean = s.get('mean', 0)
            std = s.get('std', 1)
            if std > 1e-8:
                features[:, j] = (features[:, j] - mean) / std

        return features


if __name__ == '__main__':
    if not HAS_TORCH:
        print('PyTorch not installed. Install with: pip install torch')
    else:
        print('Testing EdgerunnerDataset...')
        for tf in ['1m', '5m', '15m']:
            try:
                ds = EdgerunnerDataset(split='train', timeframe=tf,
                                       target='pnl_5m', target_binary=True)
                print(f'  [{tf}] Train size: {len(ds)}, Features: {ds.get_num_features()}')
            except Exception as e:
                print(f'  [{tf}] Error: {e}')
