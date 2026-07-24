import sys, os, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bmll_impact import (MarketImpactComputer, liquidity_removal_events, mid_from_book,
                         impact_bps, geometric_window, linear_window)

T0 = pd.Timestamp("2021-05-05 09:00:00")

def book():
    """Book where mid=100 until each event, then moves 1% the 'right' way for the aggressor."""
    rows=[]
    # quiet baseline
    for i in range(5):
        rows.append((T0 + pd.Timedelta(seconds=i), "BID", 99.0, 101.0, "INSERT", 0))
    # event A at t=10: resting ASK executed => aggressive BUY ; mid rises after
    rows.append((T0 + pd.Timedelta(seconds=10), "ASK", 99.0, 101.0, "REMOVE", 500))
    for i in range(1, 40):
        rows.append((T0 + pd.Timedelta(seconds=10, milliseconds=i*100), "BID", 100.0, 102.0, "INSERT", 0))
    # event B at t=60: resting BID executed => aggressive SELL ; mid falls after
    rows.append((T0 + pd.Timedelta(seconds=60), "BID", 100.0, 102.0, "REMOVE", 500))
    for i in range(1, 40):
        rows.append((T0 + pd.Timedelta(seconds=60, milliseconds=i*100), "BID", 99.0, 101.0, "INSERT", 0))
    df = pd.DataFrame(rows, columns=["event_timestamp","side","best_bid_price",
                                     "best_ask_price","lob_action","execution_size"])
    df["execution_price"]=100.0; df["original_order_id"]=range(len(df))
    df["market_state"]="CONTINUOUS_TRADING"
    return df.set_index("event_timestamp")

b = book()

ev = liquidity_removal_events(b)
print("events:\n", ev[["event_timestamp","side","execution_size"]].to_string(index=False))
assert len(ev)==2 and set(ev.side)=={"ASK","BID"}

im = MarketImpactComputer(events=ev, books={"X": b},
                          window=geometric_window(seconds=2, n=6))
raw = im.compute()
curve = im.mean_curve()
print("\ncurve:\n", curve.to_string(index=False))

offs = im.offsets()
assert offs[len(offs)//2] == pd.Timedelta(0), "zero offset must be centred"
assert offs == sorted(offs), "offsets must be ascending"
assert len(curve) == len(offs)

# pre-event impact ~ 0 (mid flat before each event)
pre = curve[curve.offset_seconds < 0].impact_bps
assert np.allclose(pre, 0, atol=1e-9), pre.tolist()

# post-event impact positive once the offset reaches the fixture's 100ms grid.
# Offsets below 100ms ffill to the event row itself, so 0 there is correct.
post = curve[curve.offset_seconds >= 0.1].impact_bps
assert (post > 0).all(), post.tolist()
near = curve[(curve.offset_seconds > 0) & (curve.offset_seconds < 0.1)].impact_bps
assert np.allclose(near, 0), near.tolist()

# per-event: both events individually positive at the far offset
far = max(o.total_seconds() for o in im.offsets())
per = raw[[far,"ID"]].join(ev["side"])
print("\nper-event impact at +%.3fs:" % far)
print(per.to_string(index=False))
assert (per[far] > 0).all(), "aggressor normalisation failed: signs disagree"

# magnitude sanity: mid 100 -> 101 is ~99.5bps
assert 90 < per[far].mean() < 110, per[far].mean()

# had we NOT normalised, the mean would cancel to ~0 — confirm the fix matters
naive = ((raw[far].to_numpy() * np.where(ev.side.eq("ASK"), 1, -1))).mean()
normalised = raw[far].mean()
assert abs(naive) < 1.0 < abs(normalised), (naive, normalised)  # unsigned nearly cancels

# cross-product: events from one book measured against another book
im2 = MarketImpactComputer(events=ev, books={"self": b, "other": b},
                           window=linear_window(seconds=0.1, n=5))
c2 = im2.mean_curve()
assert set(c2.ID)=={"self","other"} and len(c2)==2*len(im2.offsets())

# guardrails
try:
    MarketImpactComputer(events=ev.drop(columns=["side"]), books={"X": b}); raise SystemExit("FAIL")
except KeyError as e: assert "side" in str(e)
try:
    liquidity_removal_events(b.drop(columns=["lob_action"])); raise SystemExit("FAIL")
except KeyError as e: assert "L3" in str(e)

print("\nALL IMPACT TESTS PASSED")
