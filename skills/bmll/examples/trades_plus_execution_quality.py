#!/usr/bin/env python3
"""End-to-end execution-quality analysis on BMLL Trades Plus.

Run inside the BMLL Data Lab. Produces, for one date across the UK venue set:

  1. intraday traded notional by trade Classification
  2. intraday block-sized notional by execution venue
  3. distribution of notional by level of CBBO spread capture (PricePoint)
  4. pre/post-trade markout curves by Classification

Each step applies the validation the dataset requires rather than assuming clean
input — see ../references/trades-plus.md for why each filter is present.
"""

import sys
from pathlib import Path

import pandas as pd
import plotly.express as px

from bmll2 import get_market_data

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from bmll_markouts import run as markout_run          # noqa: E402
from bmll_checks import (                              # noqa: E402
    assert_non_empty, printable_only, drop_price_point_sentinels,
    normalise_enum_flags, describe_coverage,
)

DATE = "2025-08-22"
MICS = ["XLON", "BATE", "CHIX", "TRQX", "AQXE", "SGMX", "BOTC"]

pd.options.display.max_columns = None


def load(date: str, mics: list[str]) -> pd.DataFrame:
    """Load Trades Plus + reference for each venue and attach InstrumentType."""
    tp_parts, ref_parts = [], []
    for mic in mics:
        tp_mic = get_market_data(mic, date, "trades-plus")
        ref_mic = get_market_data(mic, date, "reference")
        # A venue with no rows here is a coverage question, not zero activity.
        assert_non_empty(tp_mic, what=f"trades-plus {mic} {date}")
        tp_parts.append(tp_mic)
        ref_parts.append(ref_mic)

    tp = pd.concat(tp_parts, ignore_index=True, copy=False)
    ref = pd.concat(ref_parts, ignore_index=True, copy=False)

    # Trades Plus carries no InstrumentType; the reference table does.
    tp = tp.merge(ref[["ListingId", "InstrumentType"]], on="ListingId", how="left")
    return normalise_enum_flags(tp)


def prepare(tp: pd.DataFrame, date: str) -> pd.DataFrame:
    """UK equities only, printable prints only, with a TradingDate column."""
    eq = tp[(tp.InstrumentType == "Equity") & (tp.Jurisdiction == "UK")].copy()
    eq = printable_only(eq)                       # Size < 0 are cancellations
    eq["TradingDate"] = pd.to_datetime(eq["TradeTimestamp"].dt.date)
    eq = eq[eq.TradingDate == date]
    assert_non_empty(eq, what=f"UK equity prints on {date}")
    print(describe_coverage(eq).to_string(index=False))
    return eq


def intraday_by_classification(eq: pd.DataFrame):
    """5-minute bars of traded notional, split by BMLL trade classification."""
    bars = eq.groupby(
        ["Classification",
         pd.Grouper(key="TradeTimestamp", freq="5min", label="right")],
        as_index=False,
    )[["TradeNotionalEUR"]].sum()

    return px.bar(bars, x="TradeTimestamp", y="TradeNotionalEUR", color="Classification",
                  template="plotly_white",
                  title="UK trades by Classification (5-minute bars)")


def intraday_blocks_by_venue(eq: pd.DataFrame):
    """When during the day is block-sized liquidity actually available, and where."""
    blocks = eq[eq.IsBlock == "TRUE"]             # string enum, not a bool
    bars = blocks.groupby(
        ["ExecutionVenue",                        # where it executed, not where reported
         pd.Grouper(key="TradeTimestamp", freq="5min", label="right")],
        as_index=False,
    )[["TradeNotionalEUR"]].sum()

    return px.bar(bars, x="TradeTimestamp", y="TradeNotionalEUR", color="ExecutionVenue",
                  template="plotly_white",
                  title="Block-sized UK trades by Execution Venue (5-minute bars)")


def spread_capture(eq: pd.DataFrame):
    """Notional by level of CBBO spread capture: 0 = at bid, 0.5 = mid, 1 = at ask."""
    d = drop_price_point_sentinels(eq)            # removes the +/-99999 rows
    d = d.assign(PricePointRounded=d.PricePoint.round(1))
    dist = d.groupby(["PricePointRounded", "ExecutionVenue"],
                     as_index=False)[["TradeNotionalEUR"]].sum()

    return px.bar(dist, x="PricePointRounded", y="TradeNotionalEUR", color="ExecutionVenue",
                  template="plotly_white",
                  title="UK trades by level of CBBO spread capture")


def markouts(eq: pd.DataFrame, group_by=("Classification",)):
    """Notional-weighted markout curves, normalised to an aggressive buyer."""
    tidy = markout_run(eq, group_by=list(group_by), benchmark="primary")

    fig = px.line(tidy, x="interval_ms", y="markout_bps", color="group",
                  template="plotly_white",
                  title="Pre/post-trade markouts vs the primary midpoint")
    fig.update_xaxes(title="Interval (ms, negative = pre-trade)")
    fig.update_yaxes(title="Markout (bps)")
    return tidy, fig


def main() -> None:
    tp = load(DATE, MICS)
    eq = prepare(tp, DATE)

    intraday_by_classification(eq).show()
    intraday_blocks_by_venue(eq).show()
    spread_capture(eq).show()

    tidy, fig = markouts(eq, group_by=("Classification",))
    fig.show()
    print(tidy.head(20).to_string(index=False))


if __name__ == "__main__":
    main()
