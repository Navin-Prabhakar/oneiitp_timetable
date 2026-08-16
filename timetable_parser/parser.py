import io
import os
import re
from pathlib import Path
import certifi
from dotenv import load_dotenv
import pandas as pd
from pymongo import MongoClient
import requests

# ==========================================
# 1. ENVIRONMENT LOADING
# ==========================================
current_dir = Path(__file__).resolve().parent
candidates = [
    current_dir / ".env",
    current_dir.parent / ".env",
    Path.cwd() / ".env",
]

for env_file in candidates:
  if env_file.is_file():
    load_dotenv(dotenv_path=env_file, override=True)
    break

sheet_id = os.getenv("SHEET_ID") or os.getenv("GOOGLE_SHEET_ID")
if not sheet_id:
  raise ValueError("❌ Neither SHEET_ID nor GOOGLE_SHEET_ID found in environment variables!")


# ==========================================
# 2. DATABASE CONNECTION
# ==========================================
def get_mongo_collection():
  client = MongoClient(os.getenv("MONGO_URI"), tlsCAFile=certifi.where())
  return client["oneiitp_db"]["schedules"]


# ==========================================
# 3. HELPER FUNCTIONS & REGEX PARSERS
# ==========================================
def expand_groups(group_str: str) -> list[str]:
  """Expands strings like 'G1-6', 'G1-G6', 'G19-24', 'G1,G2' into ['G1', 'G2', ...]."""
  groups = []
  parts = [p.strip() for p in re.split(r"[,;]", group_str) if p.strip()]

  for part in parts:
    range_match = re.match(r"^G?(\d+)\s*[-–—]\s*G?(\d+)$", part, flags=re.IGNORECASE)
    if range_match:
      start = int(range_match.group(1))
      end = int(range_match.group(2))
      step = 1 if end >= start else -1
      for num in range(start, end + step, step):
        groups.append(f"G{num}")
      continue

    single_match = re.match(r"^G?(\d+)$", part, flags=re.IGNORECASE)
    if single_match:
      groups.append(f"G{int(single_match.group(1))}")
    else:
      groups.append(part.upper())

  return sorted(list(set(groups)), key=lambda x: int(x[1:]) if x[1:].isdigit() else 999)


def parse_cell_courses(cell_text: str) -> list[tuple[str, list[str]]]:
  """Extracts courses and target student groups from multi-entry strings."""
  if not isinstance(cell_text, str) or not cell_text.strip():
    return []

  cleaned = cell_text.replace("\n", ", ").replace("\r", " ").strip()
  pattern = re.compile(
      r"([A-Z]{2,4}\s*\d{3,4}[A-Z]?)\s*[\(\[\{]([^\)\]\}]+)[\)\]\}]",
      re.IGNORECASE,
  )

  results = []
  for match in pattern.finditer(cleaned):
    course = match.group(1).replace(" ", "").upper()
    group_str = match.group(2).strip()
    expanded = expand_groups(group_str)
    if expanded:
      results.append((course, expanded))

  return results


def parse_time_slot_boundaries(time_slot: str) -> tuple[str, str]:
  """Splits '9 AM - 9.55 AM' into ('9 AM', '9.55 AM')."""
  parts = [p.strip() for p in re.split(r"[-–—]", time_slot) if p.strip()]
  if len(parts) == 2:
    return parts[0], parts[1]
  return time_slot, time_slot


def merge_consecutive_lab_slots(records: list[dict]) -> list[dict]:
  """Consolidates sequential lab periods for the same group and course into a single slot."""
  if not records:
    return []

  labs = [r for r in records if r.get("type") == "Lab"]
  others = [r for r in records if r.get("type") != "Lab"]

  lab_groups = {}
  for lab in labs:
    group_key = tuple(sorted(lab["group"]))
    key = (lab["day"], lab["courseCode"], lab["venue"], group_key)
    if key not in lab_groups:
      lab_groups[key] = []
    lab_groups[key].append(lab)

  merged_labs = []
  for key, slot_list in lab_groups.items():
    if len(slot_list) == 1:
      merged_labs.append(slot_list[0])
      continue

    start_times = []
    end_times = []
    for item in slot_list:
      s, e = parse_time_slot_boundaries(item["time"])
      start_times.append(s)
      end_times.append(e)

    combined_time = f"{start_times[0]} - {end_times[-1]}"
    merged_entry = slot_list[0].copy()
    merged_entry["time"] = combined_time
    merged_labs.append(merged_entry)

  return others + merged_labs


# ==========================================
# 4. SECTION SPECIFIC PARSERS
# ==========================================
def parse_lecture_or_lab_block(df: pd.DataFrame, start_col: int, end_col: int, block_type: str, time_col: pd.Series) -> list[dict]:
  records = []

  raw_days = df.iloc[1, start_col : end_col + 1].tolist()
  filled_days = []
  last_day = None
  for d in raw_days:
    val = str(d).strip() if pd.notna(d) and str(d).strip() else None
    if val and any(day in val for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]):
      last_day = val
    filled_days.append(last_day)

  venues = [
      str(v).strip() if pd.notna(v) and str(v).strip() else block_type
      for v in df.iloc[2, start_col : end_col + 1].tolist()
  ]

  for row_idx in range(3, len(df)):
    time_slot = time_col.iloc[row_idx]
    if not isinstance(time_slot, str) or not time_slot.strip():
      continue

    time_slot = time_slot.strip()

    for rel_col_idx, abs_col_idx in enumerate(range(start_col, end_col + 1)):
      day = filled_days[rel_col_idx]
      venue = venues[rel_col_idx]
      cell_val = df.iloc[row_idx, abs_col_idx]

      if pd.isna(cell_val):
        continue

      parsed_entries = parse_cell_courses(str(cell_val))
      for course_code, group_list in parsed_entries:
        records.append({
            "day": day,
            "time": time_slot,
            "year": 1,
            "courseCode": course_code,
            "group": group_list,
            "venue": venue,
            "type": block_type,
        })

  return records


def parse_tutorial_block(df: pd.DataFrame, tut_col_start: int) -> list[dict]:
  records = []

  for col_idx in range(tut_col_start + 1, len(df.columns)):
    header_day_time = df.iloc[1, col_idx]
    course_cell = df.iloc[2, col_idx]

    if pd.isna(header_day_time) or pd.isna(course_cell):
      continue

    header_str = str(header_day_time).strip()
    course_code = str(course_cell).strip().replace(" ", "").replace("\n", "").upper()

    if not course_code or course_code.lower() == "nan":
      continue

    match = re.match(r"^([A-Za-z]+)\s*[\(\[]?([0-9\.:\sAPMapm\-]+)[\)\]]?", header_str)
    if match:
      day = match.group(1).capitalize()
      time_slot = match.group(2).strip()
    else:
      day = header_str
      time_slot = "TBA"

    for row_idx in range(3, len(df)):
      group_val = df.iloc[row_idx, tut_col_start]
      venue_val = df.iloc[row_idx, col_idx]

      if pd.isna(group_val) or pd.isna(venue_val):
        continue

      group_clean = str(group_val).strip().upper()
      venue_clean = str(venue_val).strip()

      if not group_clean or not venue_clean or venue_clean.lower() == "nan":
        continue

      records.append({
          "day": day,
          "time": time_slot,
          "year": 1,
          "courseCode": course_code,
          "group": [group_clean],
          "venue": venue_clean,
          "type": "Tutorial",
      })

  return records


def parse_all_sections(csv_url_or_filepath: str) -> list[dict]:
  if csv_url_or_filepath.startswith("http"):
    res = requests.get(csv_url_or_filepath)
    res.raise_for_status()
    df = pd.read_csv(io.StringIO(res.content.decode("utf-8")), header=None)
  else:
    df = pd.read_csv(csv_url_or_filepath, header=None)

  row_0 = df.iloc[0].fillna("").astype(str).tolist()

  lab_start = -1
  tut_start = -1

  for idx, val in enumerate(row_0):
    val_upper = val.upper().strip()
    if "LAB" in val_upper and lab_start == -1:
      lab_start = idx
    elif "TUTORIAL" in val_upper and tut_start == -1:
      tut_start = idx

  lec_start = 1
  lec_end = (lab_start - 1) if lab_start != -1 else 15
  lab_start = lab_start if lab_start != -1 else 17
  lab_end = (tut_start - 1) if tut_start != -1 else 32
  tut_start = tut_start if tut_start != -1 else 33

  time_col = df.iloc[:, 0]
  raw_records = []

  raw_records.extend(parse_lecture_or_lab_block(df, lec_start, lec_end, "Lecture", time_col))
  raw_records.extend(parse_lecture_or_lab_block(df, lab_start, lab_end, "Lab", time_col))
  raw_records.extend(parse_tutorial_block(df, tut_start))

  return merge_consecutive_lab_slots(raw_records)


# ==========================================
# 5. SYNC EXECUTOR
# ==========================================
def sync_sheet_to_mongo():
  sheet_id = os.getenv("SHEET_ID") or os.getenv("GOOGLE_SHEET_ID")
  sheet_gid = os.getenv("SHEET_GID", "0")
  csv_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={sheet_gid}"

  records = parse_all_sections(csv_url)

  col = get_mongo_collection()
  col.delete_many({})

  if records:
    col.insert_many(records)
    col.create_index([("group", 1), ("day", 1)])

  return len(records)


if __name__ == "__main__":
  synced = sync_sheet_to_mongo()
  print(f"✅ Successfully synced {synced} total consolidated records into MongoDB Atlas.")