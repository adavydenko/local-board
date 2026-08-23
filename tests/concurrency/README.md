# Concurrency tests

The suite exercises synchronized thread and process writers creating issues
against a single shared board, optimistic-concurrency races on claims and
updates to one issue, dashboard/list_issues readers running alongside
writers, and bounded lock-retry exhaustion. Every scenario uses one `Board`
per worker against a shared temporary SQLite file; process and mixed
read/write scenarios finish with `PRAGMA integrity_check` and
`PRAGMA foreign_key_check`.
