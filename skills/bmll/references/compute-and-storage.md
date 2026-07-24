# Compute and Storage

## Storage areas

The Data Lab has a **`local`** area (the notebook instance's disk) and persistent remote areas
(`user`, `organisation`, `sftp`, `support`).

**Everything written locally is destroyed when the workspace stops.** Notebooks in every area save
into `local`, so `df.to_csv('results.csv')` produces a file that disappears — a result the user
believes is saved and is not.

Small files can be dragged out of the JupyterLab UI. Anything larger, or anything programmatic,
goes through the file management API.

```python
import bmll2 as b2
```

| Call | Purpose |
|---|---|
| `b2.create_folder(name, area=, ensure_exists=)` | Create a folder in a storage area |
| `b2.list_files(area=, path=, local=)` | List files |
| `b2.put_file(local_filename, remote_dir=, ...)` | **Persist** a local file |
| `b2.get_file(filename, area=, local_dir=, ...)` | Download to the instance |
| `b2.copy_file(old, new, ...)` | Copy within an area |
| `b2.copy_file_to_support(filepath, path=, area=)` | Share with BMLL support |
| `b2.rename_file(old, new, area=)` | Rename |
| `b2.delete_file(path, area=)` | Delete |
| `b2.delete_folder(path, area=, recursive=)` | Delete a folder |
| `b2.file_exists(path, area=, is_folder=)` | Existence check |
| `b2.list_deleted_files(path=, area=)` | Recently deleted files |
| `b2.get_file_history(path, area=)` | All versions of a file |
| `b2.recover_file_version(path, recovery_file_name, version_id=)` | Restore |

```python
b2.create_folder('analysis')
b2.put_file('analysis/results.csv')
b2.list_files(path='analysis')
b2.get_file('analysis/results.csv', local_dir='analysis')
```

### Versioning and recovery

Storage is versioned. Deleted files are recoverable for **7 days**:

```python
b2.list_deleted_files('analysis')
b2.get_file_history(path='analysis/results.csv')
# {'analysis/results.csv': [VersionInfo(version_id='t3ih...', latest=False, size=12,
#                                       last_modified=..., delete_marker=False),
#                           VersionInfo(version_id='ZFNw...', latest=True,
#                                       delete_marker=True, ...)]}
b2.recover_file_version('analysis/results.csv', recovery_file_name='recovered.csv')
```

A `delete_marker=True` entry is the deletion event, not a version of the content — recover the
`version_id` of the last real version below it. `get_file_history` accepts a filename prefix, so
partial paths return every matching file.

Recovered files land in the **same directory** as the deleted original.

## Spark dataframes

```python
from bmll2 import save_spark_dataframe, load_spark_dataframe

save_spark_dataframe(spark_df, path='/analysis/book', format='parquet', mode='overwrite')
sdf = load_spark_dataframe(path='/analysis/book', format='parquet')
```

Saves to the user, organisation or support area — persistent, unlike a local write.

## Parallelising without a cluster

A workspace has up to **192 cores**. Exhaust single-machine parallelism before reaching for a
cluster — the cluster's startup cost frequently exceeds the job.

**Dataframe engines** parallelise for free:

```python
df = get_market_data('XLON', '2025-06-23', 'trades', df_engine='polars')
df.group_by('Ticker').agg(pl.col('Size').sum())     # CPU time >> wall time
```

CPU time being a multiple of wall time is the confirmation it went parallel.

**`SparkHelper`** parallelises per-listing `Security` work, which is otherwise serial:

```python
from bmll2 import SparkHelper, Security

def get_lob_size(lid, date):
    sec = Security.from_listing_id(listing_id=lid, date=date)
    return len(sec.market_data('L3').incremental_book_L3())

args = [(lid, date) for lid in listing_ids]

with SparkHelper.track_progress():
    results = SparkHelper.map(get_lob_size, args).collect()
```

Roughly 4× on 20 large UK order books versus the serial loop; the speedup depends on the function
and the data size.

`SparkHelper` methods: `map`, `flatMap`, `foreach`, `parallelize`, `get_context`,
`track_progress`.

**`SparkHelper.map` returns results in completion order, not argument order.** Zip the inputs into
the return value rather than pairing the result list against `args` positionally — BMLL's own
tutorial demonstrates the mispairing (`pd.DataFrame(lids, results)` transposes the two).

```python
def get_lob_size(lid, date):
    sec = Security.from_listing_id(listing_id=lid, date=date)
    return (lid, len(sec.market_data('L3').incremental_book_L3()))   # carry the key
```

## Clusters

For genuinely multi-machine work:

```python
from bmll2 import create_cluster, get_clusters, ClusterConfig
```

`create_cluster` takes a configurable node count and node type, and a log path (in a user area). If
the log path is omitted, **no logging happens** — and a failed cluster job with no logs is
undiagnosable after the fact.

`get_clusters()` lists clusters with `Name`, `Status`, total running time, `Started`/`Ready`/
`Stopped`, node type and count, user, and job count.

Cluster objects: `.submit()`, `.execute()`, `.wait()`, `.describe()`, `.logs()`, `.distcp()`,
`.terminate()`. Collections: `ClusterCollection.get/describe/terminate`.

Logging helpers for code running on a cluster: `bmll2.cluster.get_logger`,
`initialise_logging`, `put_cluster_log`, `parameters`.

`stop_workspace()` stops the current workspace immediately — the programmatic equivalent of the
Stop Workspace button.

## Scheduled jobs

Jobs run notebooks or scripts on a schedule or a data-availability trigger. Manage them in the Data
Lab UI under *Jobs*, or via the API:

```python
from bmll import compute

compute.create_job(compute_type='cluster', ...)   # or 'instance'
compute.get_jobs()
compute.get_tasks()
compute.terminate_job_run(...)
```

Jobs are built from `JobTask` objects and triggers:

| Trigger | Fires on |
|---|---|
| `CronTrigger` | A cron schedule |
| `L3Availability` | Level 3 data becoming available for a market/date |

`L3Availability` is the one that matters for daily pipelines — cron fires at a wall-clock time
whether or not the data has landed, and BMLL data is T+1 with variable arrival.

Job objects (`ClusterJob`, `InstanceJob`) support `add_task`, `add_trigger`, `delete_task`,
`delete_trigger`, `execute`, `clone`, `update`, `reload`, `previous_runs`, `to_dict`, `delete`.

Jobs are **public** or **private**; public jobs are visible to the whole organisation.

`bmll2.scheduling` offers the cluster-side equivalents: `schedule_cluster`, `ClusterTask`
(`submit`, `trigger`, `delete`, `get_history`, `get_triggers_history`, `display`),
`get_cluster_tasks`, `L3Availability`.

## Credentials

`bmll2.key_management` — `store_key`, `get_keys`, `delete_key` — for third-party credentials needed
inside the Lab. Do not hard-code secrets in notebooks; they persist in the notebook file and in
version history.
