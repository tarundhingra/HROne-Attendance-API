```markdown
# HR Attendance API

This repository contains the completed FastAPI backend for the Employee Attendance & Analytics assignment. All application code is contained within a single file (`app/main.py`) as requested.

## Requirements
* Python 3.11+
* MongoDB 6.0+ 

## How to Run
1. Ensure your MongoDB instance is running locally or via Atlas.
2. Set your environment variables (a local `.env` file in the root directory is supported):
   ```text
   MONGO_URI=mongodb://localhost:27017
   MONGO_DB=hrone_db

```

3. Install the required dependencies:
```bash
pip install -r requirements.txt

```


4. Start the server:
```bash
uvicorn app.main:app --port 8000

```


*(The server will be ready on port 8000. Interactive documentation is available at `http://localhost:8000/docs`)*

## Notes for the Reviewer

* **Database Indexes:** All required compound and unique indexes are created automatically and idempotently at startup using FastAPI's `lifespan` context manager.
* **Rounding (R8):** The API strictly adheres to the half-up rounding rule for work hours and rates by utilizing Python's `Decimal` module with `ROUND_HALF_UP`, avoiding the banker's rounding behavior of the built-in `round()`.
* **Completeness:** All endpoints, including the four MongoDB aggregation analytics pipelines and the `admin/explain` endpoint, are complete and handle race conditions natively at the database level.

```

```
