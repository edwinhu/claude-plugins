#!/usr/bin/env python3
"""Pre/post-trade markouts from a BMLL Trades Plus frame.

Markouts measure market impact: how a benchmark midpoint moves in the milliseconds
and seconds around a trade. Trades Plus supplies the midpoints directly
(``PreTradeMid{X}ms``/``PostTradeMid{X}ms``, optionally ``...AtPrimary``), so the work
here is three things that are easy to get silently wrong:

1. **Side inference.** ``AggressorSide == 0`` (unknown) is common on off-book and
   auction prints, and Trades Plus supplies no inference. Left unhandled, those rows
   are either dropped or treated as a third side.
2. **Sign normalisation.** Markouts must be viewed from one perspective — by
   convention an aggressive buyer. Without negating the seller-initiated rows, buys
   and sells cancel and the curve flattens toward zero, which reads as "no impact"
   rather than "two opposite signs were averaged".
3. **Weighting.** A simple mean lets a cloud of odd-lot prints outvote the
   institutional flow the analysis is about; aggregation is notional-weighted.

Usage as a library::

    from bmll_markouts import infer_aggressor_side, compute_markouts, aggregate_markouts

    df = infer_aggressor_side(df, benchmark="primary")
    df, cols = compute_markouts(df, benchmark="primary")
    agg = aggregate_markouts(df, cols, group_by=["Classification"])
    tidy = to_long(agg, group_by=["Classification"])   # for plotting

Usage as a CLI (reads/writes parquet)::

    python bmll_markouts.py --input tp.parquet --output markouts.parquet \\
        --benchmark primary --group-by Classification
"""

from __future__ import annotations

import argparse
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

# The 15 intervals BMLL publishes, in milliseconds, ascending.
INTERVALS_MS: tuple[int, ...] = (
    1, 2, 5, 10, 25, 50, 100, 200, 500,
    1_000, 5_000, 15_000, 30_000, 60_000, 300_000,
)

# BMLL AggressorSide encoding.
SIDE_UNKNOWN = 0
SIDE_BUY_AGGRESSOR = 1   # quotes on the ASK side aggressed by an aggressive buy
SIDE_SELL_AGGRESSOR = 2  # quotes on the BID side aggressed by an aggressive sell


def _suffix(benchmark: str) -> str:
    if benchmark == "primary":
        return "AtPrimary"
    if benchmark == "consolidated":
        return ""
    raise ValueError(f"benchmark must be 'primary' or 'consolidated', got {benchmark!r}")


def benchmark_columns(benchmark: str = "primary") -> dict[str, object]:
    """Return the Trades Plus column names for a benchmark family."""
    s = _suffix(benchmark)
    return {
        "best_bid": f"BestBidPrice{s}",
        "best_ask": f"BestAskPrice{s}",
        "post_mid": f"PostTradeMid{s}",
        "pre_mids": [f"PreTradeMid{x}ms{s}" for x in reversed(INTERVALS_MS)],
        "post_mids": [f"PostTradeMid{x}ms{s}" for x in INTERVALS_MS],
    }


def _require(df: pd.DataFrame, cols: Iterable[str], what: str) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise KeyError(
            f"{what}: missing {len(missing)} column(s) from the Trades Plus frame: "
            f"{missing[:6]}{'...' if len(missing) > 6 else ''}. "
            "Check the benchmark argument and that this is a 'trades-plus' table."
        )


def pre_trade_mid(df: pd.DataFrame, benchmark: str = "primary") -> pd.Series:
    """Midpoint of the prevailing touch, as of (before) the trade."""
    c = benchmark_columns(benchmark)
    _require(df, [c["best_bid"], c["best_ask"]], "pre_trade_mid")
    return 0.5 * (df[c["best_bid"]] + df[c["best_ask"]])


def infer_aggressor_side(
    df: pd.DataFrame,
    benchmark: str = "primary",
    price_col: str = "InstrumentCurrencyPrice",
    inplace: bool = False,
) -> pd.DataFrame:
    """Fill ``AggressorSide == 0`` rows using the Lee-Ready rule.

    Price above the prevailing mid implies a buyer-initiated trade, below implies
    seller-initiated, and at the mid falls back to the tick test against the
    post-trade mid. Rows with a side already reported are left untouched.

    Adds ``PreTradeMid`` (the benchmark's prevailing midpoint) as a side effect,
    because ``compute_markouts`` needs the same series.
    """
    if not inplace:
        df = df.copy()
    c = benchmark_columns(benchmark)
    _require(df, [price_col, c["post_mid"]], "infer_aggressor_side")
    if "AggressorSide" not in df.columns:
        raise KeyError("infer_aggressor_side: frame has no 'AggressorSide' column")

    mid = pre_trade_mid(df, benchmark)
    df["PreTradeMid"] = mid

    price = df[price_col]
    next_mid = df[c["post_mid"]]

    unknown = df["AggressorSide"].eq(SIDE_UNKNOWN)
    above = price.gt(mid)
    below = price.lt(mid)
    # At the mid: uptick (or flat) implies buyer-initiated.
    uptick = next_mid.ge(mid)

    inferred = np.where(
        above, SIDE_BUY_AGGRESSOR,
        np.where(below, SIDE_SELL_AGGRESSOR,
                 np.where(uptick, SIDE_BUY_AGGRESSOR, SIDE_SELL_AGGRESSOR)),
    )
    # Leave unknown where the benchmark itself is missing — do not invent a side.
    resolvable = unknown & mid.notna() & price.notna()
    df.loc[resolvable, "AggressorSide"] = inferred[resolvable.to_numpy()]
    return df


def compute_markouts(
    df: pd.DataFrame,
    benchmark: str = "primary",
    inplace: bool = False,
) -> tuple[pd.DataFrame, list[str]]:
    """Add per-interval markout columns in basis points, normalised to an aggressive buyer.

    Each markout is ``1e4 * (mid_at_interval - pre_trade_mid) / pre_trade_mid``, with
    the sign flipped for seller-initiated trades so every row reads from the
    aggressive buyer's perspective.

    Returns ``(df, markout_cols)`` with ``markout_cols`` ordered from the earliest
    pre-trade interval to the latest post-trade interval.
    """
    if not inplace:
        df = df.copy()
    c = benchmark_columns(benchmark)
    mid_cols: list[str] = list(c["pre_mids"]) + list(c["post_mids"])
    _require(df, mid_cols, "compute_markouts")

    if "PreTradeMid" in df.columns:
        base = df["PreTradeMid"]
    else:
        base = pre_trade_mid(df, benchmark)
        df["PreTradeMid"] = base

    sell = df["AggressorSide"].eq(SIDE_SELL_AGGRESSOR)
    markout_cols: list[str] = []
    for col in mid_cols:
        out = f"Markout{col}"
        bps = 1e4 * (df[col] - base) / base
        df[out] = bps.mask(sell, -bps)
        markout_cols.append(out)
    return df, markout_cols


def aggregate_markouts(
    df: pd.DataFrame,
    markout_cols: Sequence[str],
    group_by: Sequence[str],
    weight_col: str = "TradeNotionalEUR",
) -> pd.DataFrame:
    """Notional-weighted mean markout per group.

    A simple mean would let many small prints outweigh the large ones; weighting by
    traded notional keeps the result representative of where the volume actually was.
    """
    group_by = list(group_by)
    _require(df, list(markout_cols) + group_by + [weight_col], "aggregate_markouts")

    w = df[weight_col]
    out = {}
    grouper = df.groupby(group_by, dropna=False)
    denom = grouper[weight_col].sum()
    for col in markout_cols:
        num = df[col].mul(w).groupby([df[g] for g in group_by], dropna=False).sum()
        out[col] = num / denom
    return pd.DataFrame(out).reset_index()


def to_long(
    agg: pd.DataFrame,
    group_by: Sequence[str],
    absolute: bool = False,
) -> pd.DataFrame:
    """Reshape aggregated markouts to long form with a signed interval axis.

    Pre-trade intervals become negative milliseconds, post-trade positive, so the
    result plots as a single left-to-right impact curve.
    """
    group_by = list(group_by)
    value_cols = [c for c in agg.columns if c not in group_by]

    long = agg.melt(id_vars=group_by, value_vars=value_cols,
                    var_name="_col", value_name="markout_bps")

    pre = long["_col"].str.contains("PreTradeMid")
    ms = long["_col"].str.extract(r"Mid(\d+)ms", expand=False).astype("Int64")
    long["interval_ms"] = ms.where(~pre, -ms)
    long["group"] = long[group_by].astype(str).agg("|".join, axis=1)
    if absolute:
        long["markout_bps"] = long["markout_bps"].abs()

    return (long.drop(columns=["_col"])
                .sort_values(group_by + ["interval_ms"])
                .reset_index(drop=True))


def run(
    df: pd.DataFrame,
    group_by: Sequence[str] = ("Classification",),
    benchmark: str = "primary",
    weight_col: str = "TradeNotionalEUR",
) -> pd.DataFrame:
    """Full pipeline: infer side, compute markouts, aggregate, reshape to long."""
    df = infer_aggressor_side(df, benchmark=benchmark)
    df, cols = compute_markouts(df, benchmark=benchmark)
    agg = aggregate_markouts(df, cols, group_by=group_by, weight_col=weight_col)
    return to_long(agg, group_by=group_by)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--input", required=True, help="Parquet file of a Trades Plus frame")
    p.add_argument("--output", required=True, help="Parquet file for the long-form result")
    p.add_argument("--benchmark", default="primary", choices=["primary", "consolidated"])
    p.add_argument("--group-by", nargs="+", default=["Classification"])
    p.add_argument("--weight-col", default="TradeNotionalEUR")
    args = p.parse_args()

    df = pd.read_parquet(args.input)
    out = run(df, group_by=args.group_by, benchmark=args.benchmark,
              weight_col=args.weight_col)
    out.to_parquet(args.output, index=False)
    print(f"{len(out)} rows -> {args.output}")
    print(out.head(20).to_string(index=False))


if __name__ == "__main__":
    main()
