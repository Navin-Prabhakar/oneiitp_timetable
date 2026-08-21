import os
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
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
def serve_home():
    return FileResponse(static_dir / "index.html")


@app.get("/api/timetable")
def get_schedule(
    year: Optional[int] = Query(default=None),
    branch: Optional[str] = Query(default=None),
    group: Optional[str] = Query(default=None),
    day: Optional[str] = Query(default=None),
):
    schedules_col = get_mongo_collection()
    query = {}

    if year:
        query["year"] = year
    if branch and branch.upper() != "ALL":
        query["branch"] = branch.upper()
    if group:
        query["group"] = group.upper()
    if day:
        query["day"] = day.capitalize()

    results = list(schedules_col.find(query, {"_id": 0}))
    return {
        "year": year,
        "branch": branch.upper() if branch else None,
        "group": group.upper() if group else None,
        "count": len(results),
        "schedule": results,
    }


@app.get("/api/timetable/{group}")
def get_group_timetable(group: str, day: Optional[str] = None):
    """Backward compatibility endpoint for Year 1 group searches"""
    schedules_col = get_mongo_collection()
    query = {"group": group.upper()}
    if day:
        query["day"] = day.capitalize()

    results = list(schedules_col.find(query, {"_id": 0}))
    return {"group": group.upper(), "count": len(results), "schedule": results}


@app.post("/api/sync")
def trigger_sync(x_sync_secret: Optional[str] = Header(default=None)):
    expected_secret = os.getenv("SYNC_SECRET")
    if expected_secret and x_sync_secret != expected_secret:
        raise HTTPException(status_code=403, detail="Unauthorized")

    count = sync_sheet_to_mongo()
    return {
        "status": "success",
        "records_synced": count,
        "message": f"MongoDB successfully synced {count} records across all sheets",
    }