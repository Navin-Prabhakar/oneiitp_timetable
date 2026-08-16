import os
import re
from dotenv import load_dotenv
import pandas as pd
from pymongo import MongoClient

load_dotenv()


def get_mongo_collection():
  mongo_uri = os.getenv("MONGO_URI")
  client = MongoClient(mongo_uri)
  db = client["oneiitp_db"]
  return db["schedules"]


def expand_groups(group_str):
  if not group_str or not isinstance(group_str, str):
    return ["All"]

  clean = (
      group_str.replace("(", "")
      .replace(")", "")
      .replace("Group", "")
      .replace("No", "")
      .strip()
  )
  groups = []

  for part in re.split(r"[,/]", clean):
    part = part.strip()
    match_range = re.match(r"G?(\d+)\s*-\s*G?(\d+)", part, re.IGNORECASE)
    match_single = re.match(r"G?(\d+)", part, re.IGNORECASE)

    if match_range:
      start, end = int(match_range.group(1)), int(match_range.group(2))
      groups.extend([f"G{i}" for i in range(start, end + 1)])
    elif match_single and part.isalnum():
      groups.append(f"G{match_single.group(1)}")
    elif part and not any(
        kw in part.lower() for kw in ["reserved", "practical", "nil"]
    ):
      groups.append(part.upper())

  return sorted(list(set(groups))) if groups else ["All"]


def parse_slot_cell(cell_value, day, time_slot, default_venue, session_type):
  if not cell_value or pd.isna(cell_value):
    return []

  val = str(cell_value).strip()
  if val == "" or "Reserved" in val:
    return []

  entries = []
  matches = re.findall(
      r"([A-Z]{2}\d{4}(?:[A-Z])?)(?:\s*\(([^)]+)\))?", val, re.IGNORECASE
  )

  if matches:
    for course_code, grp_text in matches:
      entries.append({
          "day": day,
          "time": time_slot,
          "year": 1,
          "courseCode": course_code.upper(),
          "group": expand_groups(grp_text),
          "venue": default_venue,
          "type": session_type,
      })
  return entries


def sync_sheet_to_mongo(sheet_id=None, sheet_name="Sheet1"):
  sheet_id = sheet_id or os.getenv("GOOGLE_SHEET_ID")
  csv_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet={sheet_name}"

  df = pd.read_csv(csv_url, header=None)
  records = []

  # Column index mapping based on your sheet layout
  column_map = [
      {"day": "Monday", "col": 1, "venue": "CLH: LT003", "type": "Lecture"},
      {"day": "Monday", "col": 2, "venue": "CLH: LT103", "type": "Lecture"},
      {"day": "Tuesday", "col": 3, "venue": "CLH: LT003", "type": "Lecture"},
      {"day": "Tuesday", "col": 4, "venue": "CLH: LT103", "type": "Lecture"},
      {"day": "Wednesday", "col": 7, "venue": "CLH: LT003", "type": "Lecture"},
      {"day": "Thursday", "col": 10, "venue": "CLH: LT003", "type": "Lecture"},
      {"day": "Friday", "col": 13, "venue": "CLH: LT003", "type": "Lecture"},
  ]

  for row_idx in range(3, min(15, len(df))):
    time_slot = df.iloc[row_idx, 0]
    if pd.isna(time_slot) or str(time_slot).strip() == "":
      continue

    for col_info in column_map:
      c_idx = col_info["col"]
      if c_idx < len(df.columns):
        cell_val = df.iloc[row_idx, c_idx]
        parsed = parse_slot_cell(
            cell_val,
            col_info["day"],
            str(time_slot),
            col_info["venue"],
            col_info["type"],
        )
        records.extend(parsed)

  schedules_col = get_mongo_collection()
  if records:
    schedules_col.delete_many({"year": 1})
    schedules_col.insert_many(records)
    return len(records)
  return 0