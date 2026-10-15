"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
import calendar
from fastapi import FastAPI, HTTPException, Query
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, field_validator, model_validator
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
from contextlib import asynccontextmanager
from pymongo import IndexModel, ASCENDING, DESCENDING
load_dotenv()

# Defect 1: Missing tz_aware=True. PyMongo returns naive datetimes otherwise.
client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"), tz_aware=True)
db = client[os.getenv("MONGO_DB", "hrone_db")]

IST = timezone(timedelta(hours=5, minutes=30))

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Idempotently create indexes at startup
    db.employees.create_index("emp_code", unique=True)
    db.employees.create_index("department")
    
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING)], unique=True)
    db.attendance_logs.create_index([("date", DESCENDING), ("emp_code", ASCENDING)])
    db.attendance_logs.create_index("status")
    yield

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0", lifespan=lifespan)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def from_ms(epoch_ms: int) -> datetime:
    """Converts epoch ms to a UTC datetime, truncating to whole seconds (R1)."""
    dt = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
    return dt.replace(microsecond=0)

def to_ms(dt: datetime) -> int:
    """Converts a UTC datetime back to epoch milliseconds."""
    return int(dt.timestamp() * 1000)

def get_ist_date(ts: datetime, shift_start: str, shift_end: str) -> str:
    """Returns the attendance date (R1), adjusting for overnight shifts."""
    ist_dt = ts.astimezone(IST)
    if shift_end <= shift_start:  # Overnight shift
        end_time = datetime.strptime(shift_end, "%H:%M").time()
        if ist_dt.time() < end_time:
            # Punch-in is before shift end, belongs to previous calendar day
            ist_dt -= timedelta(days=1)
    return ist_dt.strftime("%Y-%m-%d")

def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    # Defect 2: Mixing UTC naive dates with IST. Everything must be IST here.
    ist_in = punch_in.astimezone(IST)
    h, m = map(int, shift_start.split(":"))
    shift_start_dt = ist_in.replace(hour=h, minute=m, second=0, microsecond=0)
    
    minutes = int((ist_in - shift_start_dt).total_seconds() / 60)
    return minutes if minutes > 10 else 0

def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    # Defect 3: Python's round() does banker's rounding. R4 requires half-up.
    hours = (punch_out - punch_in).total_seconds() / 3600
    return float(Decimal(str(hours)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

def compute_overtime(punch_out: datetime, shift_end: str) -> int:
    # Defect 4: Ignored the 30-min minimum (R3), and overnight calculations were broken.
    ist_out = punch_out.astimezone(IST)
    h, m = map(int, shift_end.split(":"))
    shift_end_dt = ist_out.replace(hour=h, minute=m, second=0, microsecond=0)
    
    # If the out punch is early in the day but the shift ends late, it was an overnight shift.
    # Adjust the shift_end_dt to match the punch_out's date.
    if ist_out.time() < shift_end_dt.time() and shift_end_dt.hour > 12:
         shift_end_dt -= timedelta(days=1)
    elif ist_out.time() > shift_end_dt.time() and shift_end_dt.hour < 12 and ist_out.hour > 12:
         shift_end_dt += timedelta(days=1)

    mins = int((ist_out - shift_end_dt).total_seconds() / 60)
    return mins if mins >= 30 else 0


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str

class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"
    
    @field_validator("status")
    def validate_status(cls, v):
        if v not in ["PRESENT", "WFH", "ON_DUTY"]:
            raise ValueError("Invalid presence status")
        return v

class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    
    @field_validator("punched_at")
    def validate_time(cls, v):
        if v is not None and v < 100000000000:
            raise ValueError("punched_at must be epoch milliseconds, not seconds")
        return v
    
    @field_validator("punched_at")
    def validate_time(cls, v):
        if v is not None and v < 100000000000:
            raise ValueError("punched_at must be epoch milliseconds, not seconds")
        return v

class RegularizeIn(BaseModel):
    reason: str
    regularized_by: str
    status: Optional[str] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None

    @field_validator("reason")
    def validate_reason(cls, v):
        if not (5 <= len(v) <= 200): raise ValueError("Reason must be 5-200 chars")
        return v

    @field_validator("regularized_by")
    def validate_by(cls, v):
        if not (1 <= len(v) <= 50): raise ValueError("regularized_by must be 1-50 chars")
        return v

# --------------------------------------------------------------------------- #
# Endpoints provided (Fixed)
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    # Defect 5: Endpoint didn't actually check the database.
    try:
        db.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(503, "Database unavailable")

@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = from_ms(to_ms(datetime.now(timezone.utc)))
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    doc.pop("_id", None)
    doc["created_at"] = to_ms(doc["created_at"])
    return doc

@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = 1, page_size: int = 20):
    q = {"department": department} if department else {}
    # Defect 6: Pagination skipped page 1 entirely, and total ignored filters.
    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)
    
    cursor = db.employees.find(q, {"_id": 0}).sort("emp_code", 1).skip(skip).limit(page_size)
    items = list(cursor)
    for item in items:
        item["created_at"] = to_ms(item["created_at"])
        
    return {"items": items, "total": total, "page": page, "page_size": page_size}

@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")
        
    # Defect 7: Failed to truncate to whole seconds (R1), and timezone was naive local.
    ts = from_ms(body.punched_at) if body.punched_at else from_ms(to_ms(datetime.now(timezone.utc)))
    
    # Defect 8: ts.date() used the server's naive local timezone.
    d = get_ist_date(ts, emp["shift_start"], emp["shift_end"])
    
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"]),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    
    # Defect 9: Used find_one -> insert_one causing race conditions. Replaced with direct insert.
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")
        
    doc.pop("_id", None)
    doc["punch_in"] = to_ms(doc["punch_in"])
    return doc

@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from cannot be after date_to")

    q = {}
    if emp_code: q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from: q["date"]["$gte"] = date_from
        if date_to: q["date"]["$lte"] = date_to
    if status: q["status"] = status

    # Defect 10: Loaded 100k docs into memory. Pushed sort, skip, limit to DB.
    total = db.attendance_logs.count_documents(q)
    cursor = db.attendance_logs.find(q, {"_id": 0}).sort(
        [("date", -1), ("emp_code", 1)]
    ).skip((page - 1) * page_size).limit(page_size)
    
    items = list(cursor)
    for d in items:
        # Defect 11: Returned 'id', violating contract which says _id never appears.
        if d.get("punch_in"): d["punch_in"] = to_ms(d["punch_in"])
        if d.get("punch_out"): d["punch_out"] = to_ms(d["punch_out"])
        for h in d.get("history", []):
            h["at"] = to_ms(h["at"])
            if "punch_in" in h.get("changes", {}):
                if h["changes"]["punch_in"]["from"]: h["changes"]["punch_in"]["from"] = to_ms(h["changes"]["punch_in"]["from"])
                if h["changes"]["punch_in"]["to"]: h["changes"]["punch_in"]["to"] = to_ms(h["changes"]["punch_in"]["to"])
            if "punch_out" in h.get("changes", {}):
                if h["changes"]["punch_out"]["from"]: h["changes"]["punch_out"]["from"] = to_ms(h["changes"]["punch_out"]["from"])
                if h["changes"]["punch_out"]["to"]: h["changes"]["punch_out"]["to"] = to_ms(h["changes"]["punch_out"]["to"])

    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    out_ts = from_ms(body.punched_at) if body.punched_at else from_ms(to_ms(datetime.now(timezone.utc)))

    # Find the most recent record whose punch_in is <= out_ts
    record = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$lte": out_ts}},
        sort=[("punch_in", -1)]
    )

    if not record:
        raise HTTPException(404, "No matching open record found")
    if record["punch_out"] is not None:
        raise HTTPException(409, "Record already punched out")

    # Contract validation: punch_out > punch_in and <= 24 hours after punch_in
    if out_ts <= record["punch_in"] or (out_ts - record["punch_in"]).total_seconds() > 86400:
        raise HTTPException(422, "punch_out must be strictly after punch_in and within 24 hours")

    work_hours = compute_work_hours(record["punch_in"], out_ts)
    overtime = compute_overtime(out_ts, emp["shift_end"])
    half_day = work_hours < 4.50

    # Atomic update: only update if punch_out is STILL None
    res = db.attendance_logs.update_one(
        {"emp_code": body.emp_code, "date": record["date"], "punch_out": None},
        {"$set": {
            "punch_out": out_ts,
            "work_hours": work_hours,
            "overtime_minutes": overtime,
            "half_day": half_day
        }}
    )

    if res.modified_count == 0:
        raise HTTPException(409, "Concurrent punch-out detected")

    # Fetch and return the updated record, format for API
    updated = db.attendance_logs.find_one({"emp_code": body.emp_code, "date": record["date"]}, {"_id": 0})
    updated["punch_in"] = to_ms(updated["punch_in"])
    updated["punch_out"] = to_ms(updated["punch_out"])
    return updated

@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: str, body: RegularizeIn):
    emp = db.employees.find_one({"emp_code": emp_code})
    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not emp or not record:
        raise HTTPException(404, "Employee or record not found")

    updates = body.model_dump(exclude_unset=True)
    new_status = updates.get("status", record["status"])
    
    # Resolve punch times
    new_in = from_ms(updates["punch_in"]) if updates.get("punch_in") else record.get("punch_in")
    if "punch_in" in updates and updates["punch_in"] is None: new_in = None
    
    new_out = from_ms(updates["punch_out"]) if updates.get("punch_out") else record.get("punch_out")
    if "punch_out" in updates and updates["punch_out"] is None: new_out = None

    # Contract Validations
    if new_status in ["ABSENT", "LEAVE"]:
        if updates.get("punch_in") or updates.get("punch_out"):
            raise HTTPException(422, "Cannot supply punch times for ABSENT/LEAVE")
        new_in, new_out = None, None
    else:
        if not new_in:
            raise HTTPException(422, "Presence status requires a punch_in")
            
    if new_in:
        if get_ist_date(new_in, emp["shift_start"], emp["shift_end"]) != date:
            raise HTTPException(422, "punch_in must stay on the record's attendance date")
            
    if new_in and new_out:
        if new_out <= new_in or (new_out - new_in).total_seconds() > 86400:
            raise HTTPException(422, "punch_out must be after punch_in and within 24 hours")

    # Compute new derived fields
    derived = {
        "work_hours": compute_work_hours(new_in, new_out) if new_out else None,
        "late_minutes": compute_late_minutes(new_in, emp["shift_start"]) if new_in else 0,
        "overtime_minutes": compute_overtime(new_out, emp["shift_end"]) if new_out else 0,
        "half_day": (compute_work_hours(new_in, new_out) < 4.5) if new_out else False
    }

    # Generate the delta (what actually changed)
    changes = {}
    
    def add_change(field, old_val, new_val):
        if old_val != new_val:
            old_fmt = to_ms(old_val) if isinstance(old_val, datetime) else old_val
            new_fmt = to_ms(new_val) if isinstance(new_val, datetime) else new_val
            changes[field] = {"from": old_fmt, "to": new_fmt}

    add_change("status", record.get("status"), new_status)
    add_change("punch_in", record.get("punch_in"), new_in)
    add_change("punch_out", record.get("punch_out"), new_out)
    
    for k, v in derived.items():
        add_change(k, record.get(k), v)

    if not changes:
        raise HTTPException(422, "No fields changed")

    history_entry = {
        "at": from_ms(to_ms(datetime.now(timezone.utc))),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes
    }

    # Atomic Update
    set_fields = {"status": new_status, "punch_in": new_in, "punch_out": new_out, **derived}
    db.attendance_logs.update_one(
        {"emp_code": emp_code, "date": date},
        {"$set": set_fields, "$push": {"history": history_entry}}
    )

    # Return updated document
    updated = db.attendance_logs.find_one({"emp_code": emp_code, "date": date}, {"_id": 0})
    if updated.get("punch_in"): updated["punch_in"] = to_ms(updated["punch_in"])
    if updated.get("punch_out"): updated["punch_out"] = to_ms(updated["punch_out"])
    for h in updated.get("history", []):
        h["at"] = to_ms(h["at"])

    return updated

@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    # Validate month format (YYYY-MM)
    try:
        y, m = map(int, month.split("-"))
        _, last_day = calendar.monthrange(y, m)
    except ValueError:
        raise HTTPException(422, "Invalid month format, expected YYYY-MM")

    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    # 1. Calculate working days in Python (R7: Mon-Fri, from joined_on)
    joined_date = datetime.strptime(emp["joined_on"], "%Y-%m-%d").date()
    month_start = datetime(y, m, 1).date()
    month_end = datetime(y, m, last_day).date()
    
    start_calc = max(joined_date, month_start)
    working_days = 0
    if start_calc <= month_end:
        current = start_calc
        while current <= month_end:
            if current.weekday() < 5:  # 0=Mon, 4=Fri
                working_days += 1
            current += timedelta(days=1)

    # 2. MongoDB Aggregation for Logs
    pipeline = [
        {"$match": {
            "emp_code": emp_code,
            "date": {"$regex": f"^{month}-"}
        }},
        {"$addFields": {
            "dateObj": {"$dateFromString": {"dateString": "$date"}},
        }},
        {"$addFields": {
            # $dayOfWeek: 1 (Sun) to 7 (Sat). Weekdays are 2 to 6.
            "is_weekday": {"$in": [{"$dayOfWeek": "$dateObj"}, [2, 3, 4, 5, 6]]}
        }},
        {"$group": {
            "_id": None,
            "present_days": {
                "$sum": {
                    "$cond": [
                        # Condition: Status is present AND it's a weekday
                        {"$and": [
                            {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            "$is_weekday"
                        ]},
                        # If true: 0.5 for half_day, else 1
                        {"$cond": ["$half_day", 0.5, 1]},
                        # If false: 0
                        0
                    ]
                }
            },
            "leave_days": {
                "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
            },
            "late_count": {
                "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
            },
            "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
            "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}}
        }}
    ]

    result = list(db.attendance_logs.aggregate(pipeline))
    agg = result[0] if result else {
        "present_days": 0, "leave_days": 0, "late_count": 0,
        "total_late_minutes": 0, "total_overtime_minutes": 0
    }

    # 3. Calculate Attendance Percentage (R8)
    attendance_pct = None
    if working_days > 0:
        pct = (agg["present_days"] / working_days) * 100
        attendance_pct = float(Decimal(str(pct)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": agg["present_days"],
        "leave_days": agg["leave_days"],
        "late_count": agg["late_count"],
        "total_late_minutes": agg["total_late_minutes"],
        "total_overtime_minutes": agg["total_overtime_minutes"],
        "attendance_pct": attendance_pct
    }

@app.get("/analytics/departments/summary")
def department_summary(month: str, department: Optional[str] = None):
    try:
        y, m = map(int, month.split("-"))
        _, last_day = calendar.monthrange(y, m)
        month_end_str = f"{y:04d}-{m:02d}-{last_day:02d}"
    except ValueError:
        raise HTTPException(422, "Invalid month format")

    match_emp = {"joined_on": {"$lte": month_end_str}}
    if department:
        match_emp["department"] = department

    pipeline = [
        # 1. Start with employees who are valid for this month's headcount
        {"$match": match_emp},
        
        # 2. Join their logs for this specific month
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"emp": "$emp_code"},
            "pipeline": [
                {"$match": {
                    "$expr": {"$eq": ["$emp_code", "$$emp"]},
                    "date": {"$regex": f"^{month}-"}
                }}
            ],
            "as": "log"
        }},
        
        # 3. Flatten the logs, keeping employees who have no logs
        {"$unwind": {"path": "$log", "preserveNullAndEmptyArrays": True}},
        
        # 4. Helper fields for the group stage
        {"$addFields": {
            "is_weekday": {
                "$ifNull": [
                    {"$in": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$log.date"}}}, [2, 3, 4, 5, 6]]},
                    False
                ]
            },
            "is_present": {"$in": ["$log.status", ["PRESENT", "WFH", "ON_DUTY"]]}
        }},
        
        # 5. Group by department
        {"$group": {
            "_id": "$department",
            "emp_codes": {"$addToSet": "$emp_code"},  # Unique employees for headcount
            "present_days": {
                "$sum": {
                    "$cond": [
                        {"$and": ["$is_present", "$is_weekday"]},
                        {"$cond": ["$log.half_day", 0.5, 1]},
                        0
                    ]
                }
            },
            "avg_work_hours": {
                "$avg": {
                    "$cond": [
                        {"$and": ["$is_present", {"$ne": ["$log.work_hours", None]}]},
                        "$log.work_hours",
                        None  # $avg inherently ignores null values
                    ]
                }
            },
            "late_count": {
                "$sum": {"$cond": [{"$gt": ["$log.late_minutes", 0]}, 1, 0]}
            },
            "total_late_minutes": {
                "$sum": {"$ifNull": ["$log.late_minutes", 0]}
            },
            "leave_count": {
                "$sum": {"$cond": [{"$eq": ["$log.status", "LEAVE"]}, 1, 0]}
            },
            "on_duty_count": {
                "$sum": {"$cond": [{"$eq": ["$log.status", "ON_DUTY"]}, 1, 0]}
            }
        }},
        
        # 6. Format to match contract
        {"$project": {
            "_id": 0,
            "department": "$_id",
            "headcount": {"$size": "$emp_codes"},
            "present_days": 1,
            "avg_work_hours": 1,
            "late_count": 1,
            "total_late_minutes": 1,
            "leave_count": 1,
            "on_duty_count": 1
        }},
        {"$sort": {"department": 1}}
    ]

    results = list(db.employees.aggregate(pipeline))

    # Apply Python R8 half-up rounding for the final output
    for r in results:
        if r["avg_work_hours"] is not None:
            r["avg_work_hours"] = float(Decimal(str(r["avg_work_hours"])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    # The assignment specifies omitting departments with 0 headcount. 
    # Because of our $match, a department only has 0 headcount if it doesn't exist, so this list is already clean.
    return {"month": month, "items": results}

@app.get("/analytics/leaderboard/late")
def late_leaderboard(month: str, limit: int = 10, department: Optional[str] = None):
    try:
        y, m = map(int, month.split("-"))
    except ValueError:
        raise HTTPException(422, "Invalid month format")

    pipeline = [
        # 1. Match logs in this month where the employee was late
        {"$match": {
            "date": {"$regex": f"^{month}-"},
            "late_minutes": {"$gt": 0}
        }},
        
        # 2. Group by employee to sum late minutes
        {"$group": {
            "_id": "$emp_code",
            "total_late_minutes": {"$sum": "$late_minutes"},
            "late_count": {"$sum": 1}
        }},
        
        # 3. Lookup employee details (name, department)
        {"$lookup": {
            "from": "employees",
            "localField": "_id",
            "foreignField": "emp_code",
            "as": "emp"
        }},
        
        # 4. Ignore logs if the employee document no longer exists
        {"$unwind": "$emp"}
    ]

    # 5. Filter by department if requested
    if department:
        pipeline.append({"$match": {"emp.department": department}})

    pipeline.extend([
        # 6. Compute standard competition rank (1, 2, 2, 4) based on total late minutes
        {"$setWindowFields": {
            "sortBy": {"total_late_minutes": -1},
            "output": {
                "rank": {"$rank": {}}
            }
        }},
        
        # 7. Apply limit AFTER ranking (so ties at the cutoff are all included)
        {"$match": {"rank": {"$lte": limit}}},
        
        # 8. Sort by rank descending, then tie-break by emp_code ascending
        {"$sort": {"rank": 1, "_id": 1}},
        
        # 9. Format output
        {"$project": {
            "_id": 0,
            "rank": 1,
            "emp_code": "$_id",
            "name": "$emp.name",
            "department": "$emp.department",
            "total_late_minutes": 1,
            "late_count": 1
        }}
    ])

    results = list(db.attendance_logs.aggregate(pipeline))
    return {"month": month, "items": results}

@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to")
):
    # 1. Validation
    try:
        dt_from = datetime.strptime(from_date, "%Y-%m-%d")
        dt_to = datetime.strptime(to_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(422, "Dates must be YYYY-MM-DD")

    if dt_to < dt_from:
        raise HTTPException(422, "to cannot be before from")
    if (dt_to - dt_from).days > 92:
        raise HTTPException(422, "Date range cannot exceed 92 days")

    if not db.employees.find_one({"department": department}):
        raise HTTPException(404, "Unknown department")

    # $densify upper bound is exclusive, so we add 1 day
    dt_to_plus_1 = dt_to + timedelta(days=1)

    pipeline = [
        # 2. Generate exactly one document per day in the range
        {"$documents": [{"dummy": 1}]},
        {"$densify": {
            "field": "dateObj",
            "range": {
                "step": 1,
                "unit": "day",
                "bounds": [dt_from, dt_to_plus_1]
            }
        }},
        {"$match": {"dateObj": {"$exists": True}}},
        # 3. Format the date and determine if it's a weekday (2-6)
        {"$addFields": {
            "date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$dateObj"}},
            "is_working_day": {"$in": [{"$dayOfWeek": "$dateObj"}, [2, 3, 4, 5, 6]]}
        }},
        
        # 4. Fetch ALL employees in this department (one lookup for the whole pipeline)
        {"$lookup": {
            "from": "employees",
            "pipeline": [{"$match": {"department": department}}],
            "as": "all_emps"
        }},
        
        # 5. Fetch ALL attendance logs for this department in the date range
        {"$lookup": {
            "from": "attendance_logs",
            "pipeline": [
                {"$match": {"date": {"$gte": from_date, "$lte": to_date}}},
                {"$lookup": {
                    "from": "employees",
                    "localField": "emp_code",
                    "foreignField": "emp_code",
                    "as": "emp"
                }},
                {"$match": {"emp.department": department}}
            ],
            "as": "all_logs"
        }},
        
        # 6. For each day, calculate headcount, present_count, and late_count in memory
        {"$addFields": {
            "headcount": {
                "$size": {
                    "$filter": {
                        "input": "$all_emps",
                        "as": "e",
                        "cond": {"$lte": ["$$e.joined_on", "$date"]}
                    }
                }
            },
            "daily_logs": {
                "$filter": {
                    "input": "$all_logs",
                    "as": "log",
                    "cond": {"$eq": ["$$log.date", "$date"]}
                }
            }
        }},
        {"$addFields": {
            "present_count": {
                "$sum": {
                    "$map": {
                        "input": {
                            "$filter": {
                                "input": "$daily_logs",
                                "as": "log",
                                "cond": {"$in": ["$$log.status", ["PRESENT", "WFH", "ON_DUTY"]]}
                            }
                        },
                        "as": "log",
                        "in": {"$cond": ["$$log.half_day", 0.5, 1]}
                    }
                }
            },
            "late_count": {
                "$size": {
                    "$filter": {
                        "input": "$daily_logs",
                        "as": "log",
                        "cond": {"$gt": ["$$log.late_minutes", 0]}
                    }
                }
            }
        }},
        
        # 7. Calculate raw attendance_rate (null if weekend or 0 headcount)
        {"$addFields": {
            "attendance_rate": {
                "$cond": [
                    {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
                    {"$divide": ["$present_count", "$headcount"]},
                    None
                ]
            }
        }},
        
        # 8. Calculate 7-day moving average (up to 6 preceding rows + current)
        {"$setWindowFields": {
            "sortBy": {"dateObj": 1},
            "output": {
                "moving_avg_7d": {
                    "$avg": "$attendance_rate",
                    "window": {"documents": [-6, 0]}
                }
            }
        }},
        
        # 9. Clean up final output
        {"$project": {
            "_id": 0,
            "date": 1,
            "is_working_day": 1,
            "headcount": 1,
            "present_count": 1,
            "late_count": 1,
            "attendance_rate": 1,
            "moving_avg_7d": 1
        }}
    ]

    results = list(db.aggregate(pipeline))

    # Apply strict R8 half-up rounding (4 decimals) in Python
    for r in results:
        if r["attendance_rate"] is not None:
            r["attendance_rate"] = float(Decimal(str(r["attendance_rate"])).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
        if r["moving_avg_7d"] is not None:
            r["moving_avg_7d"] = float(Decimal(str(r["moving_avg_7d"])).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))

    return {"department": department, "items": results}

@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None, month: Optional[str] = None, department: Optional[str] = None,
    limit: int = 10, date_from: Optional[str] = None, date_to: Optional[str] = None,
    status: Optional[str] = None, from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"), page: int = 1, page_size: int = 20
):
    valid = ["attendance_list", "employee_monthly", "department_summary", "late_leaderboard", "department_trend"]
    if endpoint not in valid:
        raise HTTPException(422, f"Endpoint must be one of {valid}")

    explain_out = {}
    collection_name = "attendance_logs"

    if endpoint == "attendance_list":
        q = {}
        if emp_code: q["emp_code"] = emp_code
        if date_from or date_to:
            q["date"] = {}
            if date_from: q["date"]["$gte"] = date_from
            if date_to: q["date"]["$lte"] = date_to
        if status: q["status"] = status
        
        cursor = db.attendance_logs.find(q).sort([("date", -1), ("emp_code", 1)]).skip((page - 1) * page_size).limit(page_size)
        explain_out = cursor.explain()

    elif endpoint == "employee_monthly":
        if not emp_code or not month: raise HTTPException(422, "Missing emp_code or month")
        pipeline = [{"$match": {"emp_code": emp_code, "date": {"$regex": f"^{month}-"}}}]
        explain_out = db.command("explain", {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}}, verbosity="executionStats")

    elif endpoint == "department_summary":
        if not month: raise HTTPException(422, "Missing month")
        try:
            y, m = map(int, month.split("-"))
            _, last_day = calendar.monthrange(y, m)
            month_end_str = f"{y:04d}-{m:02d}-{last_day:02d}"
        except ValueError:
            raise HTTPException(422, "Invalid month format")
            
        match_emp = {"joined_on": {"$lte": month_end_str}}
        if department: match_emp["department"] = department
        pipeline = [{"$match": match_emp}]
        explain_out = db.command("explain", {"aggregate": "employees", "pipeline": pipeline, "cursor": {}}, verbosity="executionStats")
        collection_name = "employees"

    elif endpoint == "late_leaderboard":
        if not month: raise HTTPException(422, "Missing month")
        pipeline = [{"$match": {"date": {"$regex": f"^{month}-"}, "late_minutes": {"$gt": 0}}}]
        explain_out = db.command("explain", {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}}, verbosity="executionStats")

    elif endpoint == "department_trend":
        if not from_date or not to_date or not department: raise HTTPException(422, "Missing from, to, or department")
        # Database-level aggregations ($documents) explain slightly differently
        pipeline = [{"$documents": [{"dummy": 1}]}, {"$limit": 1}] 
        explain_out = db.command("explain", {"aggregate": 1, "pipeline": pipeline, "cursor": {}}, verbosity="executionStats")
        collection_name = "database_level"

    return {
        "endpoint": endpoint,
        "collection": collection_name,
        "explain": explain_out
    }