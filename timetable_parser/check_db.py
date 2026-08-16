import json
import os
import certifi
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI"), tlsCAFile=certifi.where())
db = client["oneiitp_db"]
col = db["schedules"]

total = col.count_documents({})
print("=" * 60)
print(f"📊 DATABASE: oneiitp_db | COLLECTION: schedules | TOTAL: {total}")
print("=" * 60)

# Option A: Clean Table View
header = f"{'DAY':<10} | {'TIME':<14} | {'COURSE':<10} | {'TYPE':<9} | {'VENUE':<14} | {'GROUPS'}"
print(header)
print("-" * 60)

for doc in col.find({}, {"_id": 0}).limit(15):
  day = doc.get("day", "-")
  time_slot = doc.get("time", "-")
  course = doc.get("courseCode", "-")
  ctype = doc.get("type", "-")
  venue = doc.get("venue", "-")
  groups = ",".join(doc.get("group", []))

  print(
      f"{day:<10} | {time_slot:<14} | {course:<10} | {ctype:<9} | {venue:<14} | {groups}"
  )

print("=" * 60)
print("💡 Showing first 15 records. Run the API query to filter by group/day.")