import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
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


@app.get("/api/timetable/{group}")
def get_group_timetable(group: str, day: Optional[str] = None):
  schedules_col = get_mongo_collection()
  query = {"group": group.upper()}
  if day:
    query["day"] = day.capitalize()

  results = list(schedules_col.find(query, {"_id": 0}))
  return {"group": group.upper(), "count": len(results), "schedule": results}


@app.post("/api/sync")
def trigger_sync(x_sync_secret: Optional[str] = Header(default=None)):
  expected_secret = os.getenv("SYNC_SECRET")
  if not expected_secret or x_sync_secret != expected_secret:
    raise HTTPException(status_code=403, detail="Unauthorized")

  count = sync_sheet_to_mongo()
  return {
      "status": "success",
      "records_synced": count,
      "message": "MongoDB successfully updated from Google Sheet",
  }