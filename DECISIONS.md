# DECISIONS.md

1. **Indexes:** I created a unique compound index on `attendance_logs(emp_code, date)` to enforce uniqueness and support the monthly/trend aggregations. I created `attendance_logs(date, emp_code)` for the paginated attendance list sort, `attendance_logs(status)` for filtering, and `employees(emp_code)` (unique) + `employees(department)`. I considered a text index on `reason`, but rejected it as the contract doesn't require text search.
2. **Punch-in race:** Both requests attempt to insert a document with the same `(emp_code, date)`. The database's unique index allows the first to succeed. The second triggers a `DuplicateKeyError` in PyMongo, which I catch and translate into a 409 Conflict.
3. **Ties:** The leaderboard returns standard competition ranks (e.g., 1, 2, 2, 4) using `$setWindowFields` with `$rank`. If two employees tie at the `limit` (e.g., both rank 10), both are returned because the `$match` filter (`rank <= limit`) runs after ranking.
4. **Headcount:** I start the pipeline on the `employees` collection and use a `$lookup` with `$unwind` (`preserveNullAndEmptyArrays: True`). This keeps employees in the pipeline even if their log array is empty.
5. **100x Data:** I would pre-aggregate the department daily trend data into a materialized view via a cron job, rather than running `$densify` and massive `$lookup`s on the fly for every request.
