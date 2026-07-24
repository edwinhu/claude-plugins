# Advanced Tutorials

`https://lab.bmlltech.com/docs/contents/tutorials/advanced.html`

Index of BMLL's advanced notebooks and where each is covered in this skill.

| Tutorial | Covered in |
|---|---|
| Advanced data access: `Security` class | [order-book-rebuilding.md](order-book-rebuilding.md) — snapshots, generators, `apply`, custom operations, order tracing |
| Advanced data access: `get_market_data` | [market-data.md](market-data.md) |
| Retail Trades | [retail.md](retail.md) |
| Market Impact with BMLL | [market-impact.md](market-impact.md) + `scripts/bmll_impact.py` |
| Advanced analytics usage | Below — threading and `Session` |
| Parallelising Data Access | [compute-and-storage.md](compute-and-storage.md) |
| Cluster management API | [compute-and-storage.md](compute-and-storage.md) |
| Sharing and Importing Code | Below |
| External Data Management | Below |
| R in the Data Lab | Below |

## Thread safety and `Session`

`bmll.reference.query` and `bmll.time_series.query` are **not threadsafe by default**. In a
multithreaded application, create an explicit `Session` per thread rather than relying on the
module-level default:

```python
from bmll import Session

session = Session()                                    # uses BMLL env vars
session = Session(username=..., key_path=..., key_passphrase=...)
```

The `Session` class handles authentication and low-level communication with the BMLL REST APIs.
Sharing the implicit default session across threads is the failure this guidance exists to prevent
— and it surfaces as corrupted or interleaved responses rather than as an exception, so it is not
obvious from a traceback.

This matters when combining `bmll` Data Feed calls with `SparkHelper.map` or any thread pool. Note
`SparkHelper` parallelises across *processes*, which sidesteps the issue; a `ThreadPoolExecutor`
does not.

## Sharing and importing code

`tutorials/notebooks/sharing_and_importing_code_on_the_BMLL_platform.html`

How to make modules written in one workspace importable in another, and share them across an
organisation. Relevant because the Data Lab's local filesystem is ephemeral — code, like data,
needs to live in a persistent area to survive a workspace stop. See
[compute-and-storage.md](compute-and-storage.md).

## External data management

`tutorials/notebooks/external_data_management.html`

Connecting the Data Lab to external sources via SFTP and cloud connections, for bringing your own
data alongside BMLL's. Storage-area mechanics are in
[compute-and-storage.md](compute-and-storage.md); admin-side configuration is under
`contents/admin/external_storage.html` and `contents/admin/sftp.html`.

## R in the Data Lab

`tutorials/notebooks/getting_started_with_R.html`

The Data Lab supports R alongside Python. The BMLL APIs are Python-first — the usual pattern is to
pull with Python, persist, then analyse in R.

## Simulation

Not part of the advanced tutorials index, but adjacent and worth knowing exists:
`contents/knowledge_centre/simulation.html` plus notebooks for a first strategy, passive execution,
portfolio and agent examples, cluster-scale simulation, auctions, and market response. BMLL
provides a backtesting simulator that replays the historical order book and models the market's
response to your orders — a different tool from the impact measurement in
[market-impact.md](market-impact.md), which measures impact that already happened.
