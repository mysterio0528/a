"""Fetch BTCUSDT 1-second klines from Binance public data (data.binance.vision).

NOTE: blocked by the network policy of the cloud session this was written in.
Run locally:  python fetch_binance.py 2026-03 2026-05
Output: data/btcusdt_1s_YYYY-MM.parquet  (columns: ts, high, low, close)
1s bars are sufficient renko resolution for d >= ~10 bps.
"""
import io
import os
import sys
import zipfile
import urllib.request

import pandas as pd

BASE = "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1s"
BASE_D = "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1s"
COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
        "qvol", "trades", "tb", "tq", "ig"]


def month_range(a, b):
    ya, ma = map(int, a.split("-"))
    yb, mb = map(int, b.split("-"))
    while (ya, ma) <= (yb, mb):
        yield f"{ya:04d}-{ma:02d}"
        ma += 1
        if ma == 13:
            ya, ma = ya + 1, 1


def fetch_month(ym, out_dir="data"):
    os.makedirs(out_dir, exist_ok=True)
    url = f"{BASE}/BTCUSDT-1s-{ym}.zip"
    print("GET", url)
    raw = urllib.request.urlopen(url, timeout=120).read()
    zf = zipfile.ZipFile(io.BytesIO(raw))
    name = zf.namelist()[0]
    df = pd.read_csv(zf.open(name), header=None, names=COLS)
    if isinstance(df.iloc[0, 0], str):  # header row present in some files
        df = df.iloc[1:].reset_index(drop=True)
    df = df.astype({"open_time": "int64", "high": "float64",
                    "low": "float64", "close": "float64"})
    ts = df["open_time"].to_numpy()
    if ts[0] > 10 ** 15:  # microseconds (spot files after 2025-01-01)
        ts = ts // 1000
    out = pd.DataFrame({"ts": ts, "high": df["high"], "low": df["low"],
                        "close": df["close"]})
    path = os.path.join(out_dir, f"btcusdt_1s_{ym}.parquet")
    out.to_parquet(path, index=False)
    print("saved", path, len(out), "rows")


if __name__ == "__main__":
    a = sys.argv[1] if len(sys.argv) > 1 else "2026-03"
    b = sys.argv[2] if len(sys.argv) > 2 else "2026-05"
    for ym in month_range(a, b):
        fetch_month(ym)
