"""Fetch XAUUSD tick data (bid/ask) from Dukascopy public datafeed.

NOTE: blocked by the network policy of the cloud session this was written in.
Run locally:  python fetch_dukascopy.py 2026-05-04 2026-06-06
Output: data/xauusd_ticks_YYYY-MM-DD.parquet (columns: ts, bid, ask)

Why this source matters: real bid AND ask -> (1) renko triggers can be evaluated
exactly the way MT5 stop orders trigger (buy stop on ask, sell stop on bid),
(2) the spread distribution by hour is measured, not assumed, which feeds the
cost model c_rt(hour) directly.
"""
import datetime as dt
import lzma
import os
import struct
import sys
import urllib.request

import pandas as pd

URL = "https://datafeed.dukascopy.com/datafeed/XAUUSD/{y}/{m:02d}/{d:02d}/{h:02d}h_ticks.bi5"
REC = struct.Struct(">IIIff")  # ms_in_hour, ask_int, bid_int, ask_vol, bid_vol


def price_scale(sample_int):
    # auto-calibrate decimal scale so gold lands in a plausible range
    for k in (3, 2, 4, 1):
        v = sample_int / 10 ** k
        if 500 < v < 20000:
            return 10 ** k
    return 1000


def fetch_day(day, out_dir="data"):
    os.makedirs(out_dir, exist_ok=True)
    rows = []
    for h in range(24):
        url = URL.format(y=day.year, m=day.month - 1, d=day.day, h=h)
        try:
            raw = urllib.request.urlopen(url, timeout=60).read()
        except Exception as e:  # 404 on holidays etc.
            continue
        if not raw:
            continue
        try:
            buf = lzma.decompress(raw)
        except lzma.LZMAError:
            continue
        base = dt.datetime(day.year, day.month, day.day, h,
                           tzinfo=dt.timezone.utc).timestamp() * 1000
        for off in range(0, len(buf) - REC.size + 1, REC.size):
            ms, a, b, av, bv = REC.unpack_from(buf, off)
            rows.append((int(base) + ms, a, b))
    if not rows:
        print(day, "no data (weekend/holiday?)")
        return
    scale = price_scale(rows[len(rows) // 2][1])
    df = pd.DataFrame(rows, columns=["ts", "ask", "bid"])
    df["ask"] = df["ask"] / scale
    df["bid"] = df["bid"] / scale
    path = os.path.join(out_dir, f"xauusd_ticks_{day:%Y-%m-%d}.parquet")
    df.to_parquet(path, index=False)
    print("saved", path, len(df), "ticks, scale", scale)


if __name__ == "__main__":
    a = dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else dt.date(2026, 5, 4)
    b = dt.date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else dt.date(2026, 6, 6)
    day = a
    while day <= b:
        if day.weekday() < 6:  # skip Saturdays only; Sunday has evening session
            fetch_day(day)
        day += dt.timedelta(days=1)
