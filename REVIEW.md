# REVIEW.md

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |

1. MongoDB Timezone Awareness (client = MongoClient(...))
PyMongo returns naive datetimes by default, which can cause Python timezone-related errors when performing datetime calculations. This issue was identified when MongoDB instants loaded without tzinfo, resulting in errors during timezone arithmetic. It was fixed by adding tz_aware=True to the MongoClient options, ensuring that retrieved datetimes are timezone-aware.

2. Incorrect Timezone Conversion (compute_late_minutes)
The function directly injected an IST time into a UTC datetime object, resulting in incorrect late-minute calculations. This was noticed when a punch-in recorded exactly on time was evaluated as 5.5 hours late. The issue was fixed by converting punch_in to IST using .astimezone(IST) before performing the comparison.

3. Incorrect Rounding of Work Hours (compute_work_hours)
The function used Python's built-in round(), which applies banker's rounding instead of the required round-half-up (R4) rule. This caused values such as 4.555 hours to produce incorrect results. The issue was resolved by using Decimal(str(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) to ensure consistent rounding to two decimal places.

4. Overtime Calculation Errors (compute_overtime)
The function did not enforce the minimum 30-minute overtime rule and failed to handle overnight shifts correctly. As a result, 15 minutes of overtime could be counted, while overnight shifts sometimes produced negative values. The fix introduced a mins >= 30 condition and adjusted the shift date when handling overnight boundaries.

5. Incomplete Database Health Check (health() endpoint)
The health endpoint returned HTTP 200 without verifying whether the database was accessible. Consequently, the API could report healthy even when the database was unavailable. This was fixed by adding db.command("ping") inside a try-except block to verify database connectivity before reporting health status.

6. Incorrect Employee Pagination and Count (list_employees)
The pagination logic used skip = page * page_size, causing the first page to be skipped when requesting page 1. Additionally, the total employee count did not account for active filters, such as department. The issue was fixed by changing the calculation to (page - 1) * page_size and using count_documents(q) to obtain the correct filtered count.

7. Incorrect Punch-In Timestamp Handling (punch_in)
The function used datetime.fromtimestamp() without explicitly specifying the timezone and failed to truncate timestamps to whole seconds. This caused a punch-in recorded at 09:40:00.900 to be incorrectly marked as late, violating the R2 requirement. The fix involved converting milliseconds to seconds by dividing by 1000, specifying tz=timezone.utc, and setting microsecond=0 to remove fractional seconds.

8. Race Condition During Punch-In (punch_in)
The function relied on a check-then-insert approach using find_one() followed by insert_one() to enforce uniqueness. When two punch-in requests arrived simultaneously for the same employee and day, both could succeed. This was fixed by relying on the database's unique constraint during insertion and catching DuplicateKeyError to handle duplicate punch-in attempts safely.

9. Incorrect Attendance Date Calculation (punch_in)
The function used ts.date(), which derives the date from the datetime's current timezone rather than the intended IST attendance date. This could assign an overnight punch-in to the wrong day when the timestamp was in UTC. The issue was addressed by introducing a get_ist_date() helper to convert timestamps to IST and correctly handle overnight shift boundaries.

10. Inefficient Attendance Pagination (list_attendance)
The function loaded the entire attendance collection into memory before applying pagination, which could cause the application to hang or crash when processing large datasets, such as 100,000 documents. The fix moved sorting, skipping, and limiting operations to the MongoDB cursor using .sort(), .skip(), and .limit(), reducing unnecessary memory usage and improving scalability.

11. Unauthorized ID Fields in Attendance Responses (list_attendance)
The function manually added an id field to the output, violating the API contract, which prohibits returning either _id or id because attendance records are identified by emp_code and date. This was fixed by removing the manual ID addition and using the MongoDB projection {"_id": 0} to exclude _id from the response.

**What I judged fine :**

* **Using synchronous `pymongo` instead of `motor`:** While asynchronous database drivers are generally preferred for high-concurrency FastAPI applications, the assignment explicitly permits either "PyMongo or Motor". The synchronous implementation is perfectly valid for this scope and avoids unnecessary `async`/`await` complexity in the aggregation pipelines.
* **The single-file architecture:** In a normal code review, having models, database configuration, helpers, and endpoints all in `main.py` would be flagged for refactoring into a modular structure (`/routers`, `/models`, etc.). However, I judged this fine here because the assignment's hard constraints strictly require that all application code lives in exactly one file.
