#!/usr/bin/env python3
"""Event-time market impact (markout curves) from a BMLL L3 order book.

Different from Trades Plus markouts (``bmll_markouts.py``), which read pre-computed
midpoint columns at fixed intervals off each trade row. This computes impact from the
book itself, which buys three things Trades Plus cannot give you:

* **Arbitrary events.** Impact around any event you can express as a query on the
  incremental book — large liquidity removals, passive inserts, cancellations — not
  only executed trades.
* **Arbitrary horizons.** Any window, including sub-millisecond, rather than the 15
  published intervals.
* **Cross-product impact.** Events from one book measured against the midpoint of
  *another* — the futures/ETF lead-lag case (a trade on the CME E-mini vs the
  midpoint of SPY on NYSE, where the response appears ~4ms later, the New
  York/Chicago round trip).

Adapted from BMLL's "Market Impact with BMLL" tutorial, with the sign convention
made explicit (see ``impact_bps``).

Usage::

    from bmll_impact import (MarketImpactComputer, liquidity_removal_events,
                             mid_from_book, impact_bps, geometric_window)

    book = (NormalisedSecurity.from_listing_id(121317, "2021-05-05")
            .market_data().incremental_book_L3().set_index("event_timestamp"))

    im = MarketImpactComputer(
        events=liquidity_removal_events(book),
        books={"VOD:XLON": book},
        impact_metric=mid_from_book,
        impact_measure=impact_bps,
        window=geometric_window(seconds=10, n=20),
    )
    curve = im.mean_curve()          # tidy: offset_seconds | ID | impact_bps
"""

from __future__ import annotations

import argparse
from typing import Callable, Mapping, Sequence

import numpy as np
import pandas as pd

# The book is already impacted at the event timestamp itself, so the pre-event
# reference is taken one nanosecond earlier.
_EPS = pd.Timedelta("1ns")


def geometric_window(seconds: float = 10.0, n: int = 20,
                     scaling: float | None = None) -> list[pd.Timedelta]:
    """Geometrically spaced offsets out to ``seconds``, densest near the event.

    Impact decays fast and most of the structure is in the first milliseconds; even
    spacing spends nearly all its resolution on the flat tail.
    """
    if scaling is None:
        scaling = 10 ** (1.0 / 3.0)
    return [pd.Timedelta(seconds=seconds * scaling ** (-n + i + 1)) for i in range(n)]


def linear_window(seconds: float = 1e-4, n: int = 80) -> list[pd.Timedelta]:
    """Evenly spaced offsets — for sub-millisecond work where decay is roughly linear."""
    return [pd.Timedelta(seconds=seconds * i) for i in range(1, n)]


def mid_from_book(book: pd.DataFrame, continuous_only: bool = True) -> pd.Series:
    """Midpoint series from an L3 incremental book indexed by ``event_timestamp``."""
    if continuous_only and "market_state" in book.columns:
        book = book.query('market_state == "CONTINUOUS_TRADING"')
    return (book["best_bid_price"] + book["best_ask_price"]) / 2


def liquidity_removal_events(book: pd.DataFrame, num_events: int = -1) -> pd.DataFrame:
    """Executions that removed liquidity, largest first, then re-sorted by time.

    ``num_events`` keeps the N largest (``-1`` keeps all) — impact of the biggest
    removals is usually the question, and it bounds the cost of the reindexing below.
    """
    if "lob_action" not in book.columns:
        raise KeyError("liquidity_removal_events: expected an L3 incremental book")

    sel = book.query("(lob_action == 'REMOVE') & (execution_size > 0)")
    cols = ["event_timestamp", "side", "execution_size", "execution_price",
            "original_order_id"]
    sel = sel.sort_values("execution_size", ascending=False)
    if num_events != -1:
        sel = sel.iloc[:num_events]
    return (sel.reset_index()[cols].drop_duplicates()
               .sort_values("event_timestamp").reset_index(drop=True))


def impact_bps(events: pd.DataFrame, reference: pd.Series, metric: pd.Series) -> pd.Series:
    """Basis-point deviation of ``metric`` from ``reference``, signed to the aggressor.

    ``events['side']`` is the side of the **resting** order that was executed, so an
    executed ASK means an aggressive **buyer** and an executed BID an aggressive
    **seller**. Normalising to the aggressor makes buy- and sell-initiated impact add
    rather than cancel; without it the mean curve collapses toward zero and reads as
    "no impact".
    """
    side = events["side"].reset_index(drop=True)
    aggressor_sign = np.where(side.astype(str).str.upper().eq("ASK"), 1.0, -1.0)
    ref = pd.Series(np.asarray(reference), dtype="float64")
    met = pd.Series(np.asarray(metric), dtype="float64")
    return ((met - ref) / ref) * aggressor_sign * 1e4


class MarketImpactComputer:
    """Compute an impact curve for a set of events against one or more books.

    Parameters
    ----------
    events : DataFrame with ``event_timestamp`` and ``side``.
    books : mapping of label -> book DataFrame indexed by ``event_timestamp``.
        More than one book measures the same events against several instruments;
        a book different from the one the events came from gives cross-product impact.
    impact_metric : book -> Series (e.g. :func:`mid_from_book`).
    impact_measure : (events, reference, metric) -> Series (e.g. :func:`impact_bps`).
    window : offsets after the event; mirrored to produce the pre-event side.
    """

    def __init__(
        self,
        events: pd.DataFrame,
        books: Mapping[str, pd.DataFrame],
        impact_metric: Callable[[pd.DataFrame], pd.Series] = mid_from_book,
        impact_measure: Callable[..., pd.Series] = impact_bps,
        window: Sequence[pd.Timedelta] | None = None,
    ):
        if "event_timestamp" not in events.columns:
            raise KeyError("MarketImpactComputer: events need an 'event_timestamp' column")
        if "side" not in events.columns:
            raise KeyError("MarketImpactComputer: events need a 'side' column")
        self.events = events.reset_index(drop=True)
        self.books = dict(books)
        self.impact_metric = impact_metric
        self.impact_measure = impact_measure
        self.window = list(window) if window is not None else geometric_window()
        self.raw_impact: pd.DataFrame | None = None

    @staticmethod
    def _shift(metric: pd.Series, offset: pd.Timedelta) -> pd.Series:
        """Shift the metric's index back by ``offset`` so an as-of lookup reads ahead."""
        return pd.Series(np.asarray(metric), index=metric.index - offset)

    def offsets(self) -> list[pd.Timedelta]:
        """Full offset axis: negative (pre-event), zero, then positive (post-event)."""
        return ([-w for w in self.window[::-1]]
                + [pd.Timedelta(seconds=0)]
                + list(self.window))

    def _impact_for_book(self, book: pd.DataFrame) -> pd.DataFrame:
        metric = self.impact_metric(book)
        metric = metric[~metric.index.duplicated(keep="last")].sort_index()

        at = np.asarray(self.events["event_timestamp"] - _EPS)
        unique = np.asarray(pd.Index(self.events["event_timestamp"] - _EPS).unique())

        reference = metric.reindex(unique, method="ffill").reindex(at, method="ffill")

        cols = {}
        for w in self.offsets():
            shifted = (self._shift(metric, w)
                       .reindex(unique, method="ffill")
                       .reindex(at))
            cols[w.total_seconds()] = self.impact_measure(
                self.events, reference.reset_index(drop=True),
                shifted.reset_index(drop=True)).to_numpy()

        out = pd.DataFrame(cols)
        out.index = self.events.index
        return out

    def compute(self) -> pd.DataFrame:
        """Per-event impact at every offset, for every book. One row per event per book."""
        frames = []
        for label, book in self.books.items():
            frames.append(pd.concat(
                [self.events["event_timestamp"], self._impact_for_book(book)], axis=1
            ).assign(ID=label))
        self.raw_impact = pd.concat(frames, ignore_index=True)
        return self.raw_impact

    def mean_curve(self) -> pd.DataFrame:
        """Mean impact across events, tidy: ``offset_seconds | ID | impact_bps``."""
        raw = self.raw_impact if self.raw_impact is not None else self.compute()
        long = raw.drop(columns=["event_timestamp"]).melt(
            id_vars=["ID"], var_name="offset_seconds", value_name="impact_bps")
        return (long.groupby(["ID", "offset_seconds"], as_index=False)["impact_bps"]
                    .mean()
                    .sort_values(["ID", "offset_seconds"])
                    .reset_index(drop=True))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--book", required=True, help="Parquet of an L3 incremental book")
    p.add_argument("--output", required=True, help="Parquet for the impact curve")
    p.add_argument("--label", default="book")
    p.add_argument("--num-events", type=int, default=-1)
    p.add_argument("--window-seconds", type=float, default=10.0)
    p.add_argument("--window-n", type=int, default=20)
    args = p.parse_args()

    book = pd.read_parquet(args.book)
    if book.index.name != "event_timestamp":
        book = book.set_index("event_timestamp")

    im = MarketImpactComputer(
        events=liquidity_removal_events(book, args.num_events),
        books={args.label: book},
        window=geometric_window(args.window_seconds, args.window_n),
    )
    curve = im.mean_curve()
    curve.to_parquet(args.output, index=False)
    print(curve.to_string(index=False))


if __name__ == "__main__":
    main()
