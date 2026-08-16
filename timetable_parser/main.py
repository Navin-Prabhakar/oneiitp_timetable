import os
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from parser import get_mongo_collection, sync_sheet_to_mongo

load_dotenv()

app = FastAPI(title="OneIITP Timetable API")


@app.get("/")
def root():
  return {"message": "OneIITP Timetable Microservice is running."}


@app.get("/api/timetable/{group}")
def get_group_timetable(group: str, day: str = None):
  schedules_col = get_mongo_collection()
  query = {"group": group.upper()}
  if day:
    query["day"] = day.capitalize()

  results = list(schedules_col.find(query, {"_id": 0}))
  return {"group": group.upper(), "count": len(results), "schedule": results}


@app.post("/api/sync")
def trigger_sync(x_sync_secret: str = Header(None)):
  expected_secret = os.getenv("SYNC_SECRET")
  if x_sync_secret != expected_secret:
    raise HTTPException(status_code=403, detail="Unauthorized")

  count = sync_sheet_to_mongo()
  return {
      "status": "success",
      "records_synced": count,
      "message": "MongoDB successfully updated from Google Sheet",
  }