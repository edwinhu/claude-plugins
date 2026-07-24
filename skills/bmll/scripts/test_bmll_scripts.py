import sys, numpy as np, pandas as pd
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from bmll_markouts import (INTERVALS_MS, benchmark_columns, infer_aggressor_side,
                           compute_markouts, aggregate_markouts, to_long, run)
import bmll_checks as bc

S = "AtPrimary"
def make(n=6):
    d = {}
    # bid/ask 100/101 -> mid 100.5
    d["BestBidPriceAtPrimary"] = [100.0]*n
    d["BestAskPriceAtPrimary"] = [101.0]*n
    d["PostTradeMidAtPrimary"] = [100.6]*n
    d["InstrumentCurrencyPrice"] = [101.0, 100.0, 100.5, 101.0, 100.0, 100.5]
    d["AggressorSide"] = [0, 0, 0, 1, 2, 1]
    d["Classification"] = ["LIT_CONTINUOUS"]*3 + ["DARK_BELOW_LIS"]*3
    d["TradeNotionalEUR"] = [1000.0, 10.0, 100.0, 10.0, 500.0, 25.0]
    d["TradeDate"] = pd.to_datetime(["2025-08-22"]*n)
    d["Printable"] = [True]*(n-1) + [False]
    d["IsBlock"] = ["TRUE","false","UNKNOWN","TRUE","FALSE","UNKNOWN"]
    d["PricePoint"] = [1.0, 0.0, 0.5, 99999.0, -99999.0, 0.5]
    d["ExecutionVenue"] = ["XLON"]*n
    df = pd.DataFrame(d)
    # midpoint columns: post-trade drifts UP (price rises after the trade)
    for x in INTERVALS_MS:
        df[f"PreTradeMid{x}ms{S}"] = 100.5
        df[f"PostTradeMid{x}ms{S}"] = 100.5 + 0.01 * (x / 1000.0)
    return df

df = make()

# --- side inference
out = infer_aggressor_side(df, benchmark="primary")
print("inferred sides:", out.AggressorSide.tolist())
assert out.AggressorSide.tolist() == [1,2,1,1,2,1], out.AggressorSide.tolist()
assert np.isclose(out.PreTradeMid.iloc[0], 100.5)

# --- markouts
out, cols = compute_markouts(out, benchmark="primary")
assert len(cols) == 30, len(cols)
# ordering: earliest pre -> latest post
assert cols[0].endswith(f"PreTradeMid300000ms{S}"), cols[0]
assert cols[14].endswith(f"PreTradeMid1ms{S}"), cols[14]
assert cols[15].endswith(f"PostTradeMid1ms{S}"), cols[15]
assert cols[-1].endswith(f"PostTradeMid300000ms{S}"), cols[-1]

post60 = f"MarkoutPostTradeMid60000ms{S}"
raw_bps = 1e4 * ((100.5 + 0.01*60) - 100.5) / 100.5
buy = out.loc[out.AggressorSide==1, post60]
sell = out.loc[out.AggressorSide==2, post60]
print(f"raw bps={raw_bps:.3f}  buy={buy.iloc[0]:.3f}  sell={sell.iloc[0]:.3f}")
assert np.allclose(buy, raw_bps), buy.tolist()
assert np.allclose(sell, -raw_bps), sell.tolist()   # sign flipped for sell-aggressor
# pre-trade markouts are 0 here (pre mids == base)
assert np.allclose(out[f"MarkoutPreTradeMid1ms{S}"], 0.0)

# --- aggregation is notional-weighted, not a simple mean
agg = aggregate_markouts(out, cols, group_by=["Classification"])
print(agg[["Classification", post60]].to_string(index=False))
lit = out[out.Classification=="LIT_CONTINUOUS"]
expect = (lit[post60]*lit.TradeNotionalEUR).sum()/lit.TradeNotionalEUR.sum()
got = agg.loc[agg.Classification=="LIT_CONTINUOUS", post60].iloc[0]
assert np.isclose(got, expect), (got, expect)
assert not np.isclose(got, lit[post60].mean())  # proves weighting differs from plain mean

# --- long form
long = to_long(agg, group_by=["Classification"])
assert long.interval_ms.min() == -300000 and long.interval_ms.max() == 300000
assert len(long) == 2*30
neg = long[(long.Classification=="LIT_CONTINUOUS") & (long.interval_ms==-1000)]
assert len(neg)==1
print("long-form head:\n", long.head(4).to_string(index=False))

# --- consolidated benchmark raises clearly when columns absent
try:
    compute_markouts(df, benchmark="consolidated")
    raise SystemExit("FAIL: expected KeyError")
except KeyError as e:
    assert "trades-plus" in str(e)

# --- checks module
p = bc.printable_only(df, warn=False)
assert len(p)==5
try:
    bc.assert_non_empty(df.iloc[:0]); raise SystemExit("FAIL")
except bc.EmptyResultError: pass
try:
    bc.assert_non_empty_per_date(df, ["2025-08-22","2025-08-25"]); raise SystemExit("FAIL")
except bc.EmptyResultError as e:
    assert "2025-08-25" in str(e)
bc.assert_non_empty_per_date(df, ["2025-08-22"])
pp = bc.drop_price_point_sentinels(df)
assert len(pp)==4 and pp.PricePoint.abs().max()<=2
ne = bc.normalise_enum_flags(df)
assert ne.IsBlock.tolist()==["TRUE","FALSE","UNKNOWN","TRUE","FALSE","UNKNOWN"]
r = df.copy(); r["TradeTimestampNanoseconds"]=[1_700_000_000_000_000_000]*6
r = bc.coerce_range_timestamps(r)
assert pd.api.types.is_datetime64_any_dtype(r.TradeTimestampNanoseconds)
r2 = bc.coerce_range_timestamps(r)  # idempotent
assert pd.api.types.is_datetime64_any_dtype(r2.TradeTimestampNanoseconds)
cov = bc.describe_coverage(df)
assert cov.rows.sum()==6

# --- end-to-end run()
tidy = run(make(), group_by=["Classification"])
assert len(tidy)==60 and tidy.markout_bps.notna().all()
print("\nALL TESTS PASSED")
