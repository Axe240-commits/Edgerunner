"""HL Funding Logger — sammelt Hyperliquid Funding/OI als eigene Zeitreihe.

Zweck: Der Live-Sensor (Whale-Pulse-Widget) liest HL-Funding seit 29.07.2026.
Der Whale+Funding-Backtest (Roadmap ~Okt 2026) braucht EXAKT denselben
Sensor historisch — funding.db enthaelt Binance-Funding, das darf nach
Anti-Pattern nicht mit HL gemischt werden. Historie ist nachtraeglich nicht
beschaffbar (S3-Archive: monatlich, lueckig, keine Garantie), darum wird
ab jetzt mitgeschrieben.

Speicher: eigene DB hl_funding.db (NIEMALS in edgerunner.db oder funding.db
schreiben — Trennung der Quellen ist Regel). Research liest read-only.

Betrieb: auf dem Windows-PC (192.168.0.199), ein Prozess, Dauerlauf.
Schreibt jede volle Stunde + 90 s (HL-Funding wird stuendlich gesetzt).

Zugriffe: POST https://api.hyperliquid.xyz/info (read-only, keine Auth).
Ersteller: Hermes, 29.07.2026 — auf Alberts Entscheidung.
"""

import json
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "hl_funding.db"
HL_INFO = "https://api.hyperliquid.xyz/info"
COINS = ("BTC", "ETH")  # BTC ist Hauptbuch; ETH kostet nichts extra (gleiche Antwort)
POLL_OFFSET_S = 90      # nach der vollen Stunde warten, bis der Print steht
RETRY_S = 30
MAX_RETRIES = 3


def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS prints (
            ts INTEGER NOT NULL,            -- epoch ms (Edgerunner-Konvention)
            coin TEXT NOT NULL,
            funding REAL NOT NULL,          -- letzte Stundensatz (Dezimal: 0.0001 = 0.01 %)
            open_interest REAL,             -- in Coin-Einheiten
            mark_px REAL,
            oracle_px REAL,
            premium REAL,
            PRIMARY KEY (ts, coin)
        )
        """
    )
    conn.commit()


def fetch_ctxs() -> dict:
    """metaAndAssetCtxs holen: {coin_name: asset_ctx}."""
    body = json.dumps({"type": "metaAndAssetCtxs"}).encode()
    req = urllib.request.Request(
        HL_INFO, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        meta, ctxs = json.loads(resp.read())
    out = {}
    for i, coin in enumerate(meta.get("universe") or []):
        name = coin.get("name")
        if name in COINS and i < len(ctxs):
            out[name] = ctxs[i] or {}
    return out


def store(conn: sqlite3.Connection, ctxs: dict) -> int:
    ts = int(time.time() * 1000)
    n = 0
    for coin, ctx in ctxs.items():
        funding = ctx.get("funding")
        if funding is None:
            continue
        conn.execute(
            """
            INSERT OR REPLACE INTO prints
                (ts, coin, funding, open_interest, mark_px, oracle_px, premium)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts,
                coin,
                float(funding),
                float(ctx.get("openInterest") or 0),
                float(ctx.get("markPx") or 0),
                float(ctx.get("oraclePx") or 0),
                float(ctx.get("premium") or 0),
            ),
        )
        n += 1
    conn.commit()
    return n


def seconds_to_next_hour() -> float:
    now = time.time()
    next_hour = (int(now // 3600) + 1) * 3600
    return next_hour + POLL_OFFSET_S - now


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    _init_db(conn)
    print(f"[hl-funding] schreibe nach {DB_PATH} (Coins: {', '.join(COINS)})", flush=True)
    while True:
        wait = seconds_to_next_hour()
        print(f"[hl-funding] naechster Lauf in {int(wait)} s", flush=True)
        time.sleep(max(5, wait))
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                ctxs = fetch_ctxs()
                n = store(conn, ctxs)
                print(f"[hl-funding] {n} prints gespeichert ({time.strftime('%H:%M:%S')})", flush=True)
                break
            except Exception as exc:  # noqa: BLE001 — Dauerlauf darf nie sterben
                print(f"[hl-funding] Versuch {attempt}/{MAX_RETRIES} fehlgeschlagen: {exc}", flush=True)
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_S)
        # nach MAX_RETRIES Fehlversuchen: Stunde auslassen, naechste Runde abwarten


if __name__ == "__main__":
    if "--once" in sys.argv:
        # Einmal-Modus fuer die Aufgabenplanung (stuendlicher Task statt
        # Dauerprozess): holen, speichern, beenden. Kein Haenger moeglich.
        conn = sqlite3.connect(DB_PATH)
        _init_db(conn)
        n = store(conn, fetch_ctxs())
        print(f"[hl-funding] {n} prints gespeichert ({time.strftime('%H:%M:%S')})", flush=True)
    else:
        main()
