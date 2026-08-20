# Concurrency tests

The suite exercises synchronized thread and process writers, optimistic
conflicts on a shared issue, related-record and activity writes, bounded lock
retry exhaustion, and dashboard/activity readers running alongside writers.
Every stress scenario uses one `Board` per worker and a shared temporary SQLite
file; process and mixed read/write scenarios finish with integrity checks.
