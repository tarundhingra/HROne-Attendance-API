# REVIEW.md

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |

| 1 | `client = MongoClient(...)` | PyMongo returns naive datetimes by default. | MongoDB instants load without `tzinfo`, causing Python timezone math errors. | Added `tz_aware=True` to the `MongoClient` options. |
| 2 | `compute_late_minutes` | Injects an IST time into a UTC object directly. | A punch-in exactly on time evaluated as 5.5 hours late. | Converted `punch_in` to IST via `.astimezone(IST)` before comparing. |
| 3 | `compute_work_hours` | Uses Python's `round()`, which applies banker's rounding (rounds to even). | 4.555 hours rounded to 4.56 or 4.55 unpredictably; fails R4 half-up rule. | Used `Decimal(str(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)`. |
| 4 | `compute_overtime` | Missing the 30-minute minimum rule, and fails on overnight shifts. | 15 mins overtime incorrectly returned as 15; overnight shifts gave negative values. | Added `if mins >= 30` check and adjusted the shift date for overnight boundaries. |
| 5 | `health()` endpoint | Returns 200 without checking the database. | The DB stops, but the API still reports it is healthy. | Added `db.command("ping")` inside a try-except block. |
| 6 | `list_employees` | `skip = page * page_size` skips the first page entirely. `total` ignores filters. | Requesting page 1 skips records 0-19. Department filter count is wrong. | Changed to `(page - 1) * page_size` and used `count_documents(q)`. |
| 7 | `punch_in` | Uses `datetime.fromtimestamp` naively and fails to truncate to whole seconds. | `09:40:00.900` calculates as late (R2 violation). | Divided by 1000, added `tz=timezone.utc`, and replaced `microsecond=0`. |
| 8 | `punch_in` | Relies on check-then-insert `find_one` -> `insert_one` for uniqueness. | Two simultaneous punch-ins for the same day both succeed (race condition). | Rely directly on DB insert and catch `DuplicateKeyError`. |
| 9 | `punch_in` | `ts.date()` uses the server's local timezone. | An overnight punch-in in UTC looks like a different day than it is in IST. | Created `get_ist_date()` to convert to IST and handle overnight shift boundary shifts. |
| 10 | `list_attendance` | Loads the entire collection into memory to paginate. | App crashes or hangs on a 100k document collection. | Pushed `.sort()`, `.skip()`, and `.limit()` down to the MongoDB cursor. |
| 11 | `list_attendance` | Mutates the output to return an `id` field. | The contract explicitly prohibits returning `_id` or `id` (addressed by emp_code + date). | Removed the manual id addition and suppressed `_id` via DB projection `{"_id": 0}`. |


**What I judged fine :**

* **Using synchronous `pymongo` instead of `motor`:** While asynchronous database drivers are generally preferred for high-concurrency FastAPI applications, the assignment explicitly permits either "PyMongo or Motor". The synchronous implementation is perfectly valid for this scope and avoids unnecessary `async`/`await` complexity in the aggregation pipelines.
* **The single-file architecture:** In a normal code review, having models, database configuration, helpers, and endpoints all in `main.py` would be flagged for refactoring into a modular structure (`/routers`, `/models`, etc.). However, I judged this fine here because the assignment's hard constraints strictly require that all application code lives in exactly one file.