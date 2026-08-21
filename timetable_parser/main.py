import os
import re
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from parser import get_mongo_collection, sync_sheet_to_mongo

load_dotenv()

app = FastAPI(title="OneIITP Timetable API")

static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
def serve_home():
    index_path = static_dir / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="index.html not found in static folder")
    return FileResponse(index_path)


@app.get("/api/electives")
def get_available_electives(year: Optional[int] = Query(default=None)):
    """Returns a unique list of elective courses for frontend dropdown selection."""
    schedules_col = get_mongo_collection()
    query = {"isElective": True}
    if year:
        query["year"] = year

    results = list(schedules_col.find(query, {"courseCode": 1, "courseName": 1, "_id": 0}))

    unique_electives = {}
    for item in results:
        code = item.get("courseCode")
        if code and code not in unique_electives:
            unique_electives[code] = item.get("courseName") or code

    return [{"code": k, "name": v} for k, v in sorted(unique_electives.items())]


@app.get("/api/timetable")
def get_schedule(
    year: Optional[int] = Query(default=None),
    branch: Optional[str] = Query(default=None),
    group: Optional[str] = Query(default=None),
    day: Optional[str] = Query(default=None),
    electives: Optional[str] = Query(default=None),
):
    schedules_col = get_mongo_collection()
    or_conditions = []

    # 1. Base query for core branch classes
    branch_query = {"isElective": {"$ne": True}}
    if year:
        branch_query["year"] = year
    if branch and branch.upper() != "ALL":
        branch_query["branch"] = branch.upper()
    if group:
        branch_query["group"] = group.upper()

    or_conditions.append(branch_query)

    # 2. Query for selected electives
    if electives:
        chosen_codes = [c.strip() for c in electives.split(",") if c.strip()]
        if chosen_codes:
            # Case-insensitive prefix matching for elective course codes
            regex_patterns = [re.compile(f"^{re.escape(c)}", re.IGNORECASE) for c in chosen_codes]
            elective_query = {
                "isElective": True,
                "courseCode": {"$in": regex_patterns}
            }
            if year:
                elective_query["year"] = year
            or_conditions.append(elective_query)

    # Combine with AND logic to ensure 'day' applies globally across core and elective branches
    and_conditions = [{"$or": or_conditions}]
    if day:
        and_conditions.append({"day": {"$regex": f"^{day.strip()}$", "$options": "i"}})

    final_query = {"$and": and_conditions}

    results = list(schedules_col.find(final_query, {"_id": 0}))
    return {
        "year": year,
        "branch": branch.upper() if branch else None,
        "count": len(results),
        "schedule": results,
    }


@app.post("/api/sync")
def trigger_sync(x_sync_secret: Optional[str] = Header(default=None)):
    expected_secret = os.getenv("SYNC_SECRET")

    # Secure endpoint by default: block if SYNC_SECRET is missing or mismatched
    if not expected_secret or x_sync_secret != expected_secret:
        raise HTTPException(status_code=403, detail="Unauthorized: Invalid or missing sync secret")

    count = sync_sheet_to_mongo()
    return {
        "status": "success",
        "records_synced": count,
        "message": f"MongoDB successfully synced {count} records across all sheets",
    }