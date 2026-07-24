#!/usr/bin/env python3
"""Validation helpers for BMLL frames.

BMLL returns an empty frame rather than an error for a valid-but-wrong MIC, a
non-trading date, a listing that was not alive, or a missing entitlement. These
helpers turn that silence into an exception or an explicit report, so a hole in the
data cannot pass downstream as a zero.

Usage::

    from bmll_checks import (
        assert_non_empty_per_date, printable_only, drop_price_point_sentinels,
        normalise_enum_flags, coerce_range_timestamps, describe_coverage,
    )

    df = printable_only(df)
    assert_non_empty_per_date(df, expected_dates, date_col="TradeDate")
"""

from __future__ import annotations

from typing import Iterable, Sequence

import pandas as pd

# Trades Plus string-enum columns. Comparing these to a Python bool silently
# yields an empty frame, so they are normalised explicitly.
STRING_ENUM_COLS: tuple[str, ...] = (
    "IsBlock", "LotType", "ParticipantType", "BMLLParticipantType",
)

# PricePoint sentinels used when best bid == best ask.
PRICE_POINT_SENTINEL = 99999.0


class EmptyResultError(AssertionError):
    """Raised when a BMLL query returned no rows where rows were expected."""


def assert_non_empty(df: pd.DataFrame, what: str = "query") -> pd.DataFrame:
    """Raise if the frame is empty. Empty means 'asked wrong' as often as 'no trades'."""
    if len(df) == 0:
        raise EmptyResultError(
            f"{what} returned 0 rows. BMLL does not error on a valid-but-wrong MIC, a "
            "non-trading date, a listing that was not alive, or a missing entitlement — "
            "check reference.availability() before treating this as zero activity."
        )
    return df


def assert_non_empty_per_date(
    df: pd.DataFrame,
    expected_dates: Iterable,
    date_col: str = "TradeDate",
) -> pd.DataFrame:
    """Raise if any expected date is missing from the frame.

    An overall row count hides a single blank date inside a range; per-date is the
    granularity at which the gap actually appears.
    """
    if date_col not in df.columns:
        raise KeyError(f"assert_non_empty_per_date: no column {date_col!r}")
    present = set(pd.to_datetime(pd.Series(list(df[date_col].unique()))).dt.date)
    expected = set(pd.to_datetime(pd.Series(list(expected_dates))).dt.date)
    missing = sorted(expected - present)
    if missing:
        raise EmptyResultError(
            f"{len(missing)} expected date(s) returned no rows: "
            f"{[str(d) for d in missing[:10]]}"
            f"{'...' if len(missing) > 10 else ''}. "
            "Verify these were trading days and that the venue has coverage."
        )
    return df


def printable_only(df: pd.DataFrame, warn: bool = True) -> pd.DataFrame:
    """Keep only prints that count toward cumulative volume.

    ``Size`` is negative for cancellations and ``Printable`` is the inclusion flag;
    summing without this filter is wrong in both directions at once.
    """
    if "Printable" not in df.columns:
        if warn:
            print("printable_only: no 'Printable' column — returning frame unchanged")
        return df
    out = df[df["Printable"] == True]  # noqa: E712 — may be object dtype
    if warn:
        dropped = len(df) - len(out)
        if dropped:
            print(f"printable_only: dropped {dropped:,} non-printable rows "
                  f"({dropped / len(df):.2%})")
    return out


def drop_price_point_sentinels(
    df: pd.DataFrame,
    col: str = "PricePoint",
    lower: float = -2.0,
    upper: float = 2.0,
) -> pd.DataFrame:
    """Filter PricePoint to a plausible band.

    ``±99999`` is emitted when best bid equals best ask; an unfiltered mean is
    dominated by those rows and is not a spread-capture number.
    """
    if col not in df.columns:
        raise KeyError(f"drop_price_point_sentinels: no column {col!r}")
    return df[df[col].between(lower, upper)]


def normalise_enum_flags(
    df: pd.DataFrame,
    cols: Sequence[str] = STRING_ENUM_COLS,
    inplace: bool = False,
) -> pd.DataFrame:
    """Upper-case and strip the string-enum columns so comparisons are predictable.

    Does **not** cast to bool: ``UNKNOWN`` is a real level and collapsing it into
    ``False`` overstates the confident negatives.
    """
    if not inplace:
        df = df.copy()
    for c in cols:
        if c in df.columns:
            df[c] = df[c].astype("string").str.strip().str.upper()
    return df


def coerce_range_timestamps(
    df: pd.DataFrame,
    cols: Sequence[str] = ("TradeTimestampNanoseconds", "PublicationTimestampNanoseconds"),
    inplace: bool = False,
) -> pd.DataFrame:
    """Convert integer-nanosecond timestamps from ``get_market_data_range`` to datetimes.

    ``get_market_data`` returns these as ``datetime64``; the Spark-backed ``_range``
    variant returns integer nanoseconds. Columns already datetime-typed are skipped,
    so this is safe to call on either.
    """
    if not inplace:
        df = df.copy()
    for c in cols:
        if c in df.columns and not pd.api.types.is_datetime64_any_dtype(df[c]):
            df[c] = pd.to_datetime(df[c], unit="ns", utc=True, errors="coerce")
    return df


def describe_coverage(
    df: pd.DataFrame,
    date_col: str = "TradeDate",
    venue_col: str = "ExecutionVenue",
) -> pd.DataFrame:
    """Row counts per date and venue — the cheapest way to see a hole before aggregating."""
    keys = [c for c in (date_col, venue_col) if c in df.columns]
    if not keys:
        raise KeyError(f"describe_coverage: neither {date_col!r} nor {venue_col!r} present")
    return (df.groupby(keys, dropna=False)
              .size()
              .rename("rows")
              .reset_index()
              .sort_values(keys))
