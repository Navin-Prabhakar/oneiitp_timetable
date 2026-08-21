import io
import os
import re
from pathlib import Path
import certifi
import dns.resolver
from dotenv import load_dotenv
import pandas as pd
from pymongo import MongoClient
import requests

from courses import lookup_course_name

# ==========================================
# 0. DNS RESOLVER FIX (Bypasses Campus SRV Blocks)
# ==========================================
try:
    custom_resolver = dns.resolver.Resolver(configure=False)
    custom_resolver.nameservers = ["8.8.8.8", "1.1.1.1", "8.8.4.4"]
    dns.resolver.default_resolver = custom_resolver
except Exception:
    pass

# ==========================================
# 1. ENVIRONMENT LOADING & SHEET CONFIGS
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

sheet_id = (
    os.getenv("SHEET_ID")
    or os.getenv("GOOGLE_SHEET_ID")
    or "1K2JJyx6rupTrwnurrauCri7gow34N2d_oqbmUMmXFIo"
)

SHEET_CONFIGS = [
    {"year": 1, "branch": "ALL", "gid": "0", "is_elective": False},
    {"year": 2, "branch": "CS", "gid": "622034002", "is_elective": False},
    {"year": 2, "branch": "AI", "gid": "1192828503", "is_elective": False},
    {"year": 2, "branch": "EC", "gid": "424921665", "is_elective": False},
    {"year": 2, "branch": "EE", "gid": "743846046", "is_elective": False},
    {"year": 2, "branch": "ME", "gid": "137854804", "is_elective": False},
    {"year": 2, "branch": "CE", "gid": "117130647", "is_elective": False},
    {"year": 2, "branch": "CB", "gid": "16856677", "is_elective": False},
    {"year": 2, "branch": "MM", "gid": "152376159", "is_elective": False},
    {"year": 2, "branch": "PH", "gid": "176246211", "is_elective": False},
    {"year": 2, "branch": "MC", "gid": "30201283", "is_elective": False},
    {"year": 2, "branch": "CT", "gid": "986905833", "is_elective": False},
    {"year": 2, "branch": "ES", "gid": "72641676", "is_elective": False},
    # Electives
    {"year": 2, "branch": "ELECTIVE", "gid": "320515251", "is_elective": True},
    {"year": 3, "branch": "ELECTIVE", "gid": "12295155", "is_elective": True},
]

# ==========================================
# 2. DATABASE CONNECTION
# ==========================================
_mongo_client = None


def get_mongo_collection():
    global _mongo_client
    if _mongo_client is None:
        mongo_uri = os.getenv("MONGO_URI")
        if not mongo_uri:
            raise ValueError("❌ MONGO_URI is missing in environment variables!")
        _mongo_client = MongoClient(
            mongo_uri,
            tlsCAFile=certifi.where(),
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            socketTimeoutMS=5000,
        )
    return _mongo_client["oneiitp_db"]["schedules"]


# ==========================================
# 3. HELPER FUNCTIONS & REGEX PARSERS
# ==========================================
def expand_groups(group_str: str) -> list[str]:
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

    return sorted(list(set(groups)), key=lambda x: int(x[1:]) if len(x) > 1 and x[1:].isdigit() else 999)


def parse_cell_courses(cell_text: str, default_group: str = "ALL", is_first_year: bool = False) -> list[tuple[str, list[str]]]:
    """
    Extracts course info from a cell without splitting compound courses.
    - Year 1: Extracts clean course code and expands student groups from brackets (e.g. CS101(G1-6)).
    - Year 2+: Keeps the entire cell text intact as one course (e.g. 'EC2101/EE2101 (G1/G2)').
    """
    if not isinstance(cell_text, str) or not cell_text.strip():
        return []

    cleaned = " ".join(cell_text.split()).strip()

    if is_first_year:
        pattern_with_group = re.compile(
            r"([A-Z]{2,4}\s*\d{3,4}[A-Z]?)\s*[\(\[\{]([^\)\]\}]+)[\)\]\}]",
            re.IGNORECASE,
        )
        matches = list(pattern_with_group.finditer(cleaned))
        if matches:
            results = []
            for match in matches:
                course = match.group(1).replace(" ", "").upper()
                group_str = match.group(2).strip()
                expanded = expand_groups(group_str)
                if expanded:
                    results.append((course, expanded))
            return results

        plain_pattern = re.compile(r"\b([A-Z]{2,4}\s*\d{3,4}[A-Z]?)\b", re.IGNORECASE)
        plain_matches = plain_pattern.findall(cleaned)
        if plain_matches:
            return [(c.replace(" ", "").upper(), [default_group]) for c in plain_matches]

    # Year 2, Year 3, and Electives: Return the exact cell text as a single course
    return [(cleaned, [default_group])]


def parse_time_slot_boundaries(time_slot: str) -> tuple[str, str]:
    parts = [p.strip() for p in re.split(r"[-–—]", time_slot) if p.strip()]
    if len(parts) == 2:
        return parts[0], parts[1]
    return time_slot, time_slot


def merge_consecutive_lab_slots(records: list[dict]) -> list[dict]:
    if not records:
        return []

    labs = [r for r in records if r.get("type") == "Lab"]
    others = [r for r in records if r.get("type") != "Lab"]

    lab_groups = {}
    for lab in labs:
        group_key = tuple(sorted(lab["group"]))
        key = (lab["day"], lab["courseCode"], lab["venue"], lab["branch"], lab["year"], group_key)
        lab_groups.setdefault(key, []).append(lab)

    merged_labs = []
    for key, slot_list in lab_groups.items():
        if len(slot_list) == 1:
            merged_labs.append(slot_list[0])
            continue

        start_times = [parse_time_slot_boundaries(item["time"])[0] for item in slot_list]
        end_times = [parse_time_slot_boundaries(item["time"])[1] for item in slot_list]

        combined_time = f"{start_times[0]} - {end_times[-1]}"
        merged_entry = slot_list[0].copy()
        merged_entry["time"] = combined_time
        merged_labs.append(merged_entry)

    return others + merged_labs


# ==========================================
# 4. SECTION & ELECTIVE PARSERS
# ==========================================
def parse_elective_sheet(df: pd.DataFrame, year: int) -> list[dict]:
    """
    Explicit parser for Elective sheets:
    Reads every column across all days and venues directly without splitting lab/tutorial sections.
    """
    records = []
    total_cols = df.shape[1]
    if total_cols < 2:
        return records

    time_col = df.iloc[:, 0]

    # Row 1: Day names
    raw_days = df.iloc[1, 1:].tolist()
    filled_days = []
    last_day = None
    for d in raw_days:
        val = str(d).strip() if pd.notna(d) and str(d).strip() else None
        if val and any(day in val for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]):
            last_day = val
        filled_days.append(last_day)

    # Row 2: Venues
    raw_venues = df.iloc[2, 1:].tolist()
    venues = [
        str(v).strip() if pd.notna(v) and str(v).strip() and str(v).lower() != "nan" else "Elective Slot"
        for v in raw_venues
    ]

    # Rows 3+: Class slots
    for row_idx in range(3, len(df)):
        time_slot = time_col.iloc[row_idx]
        if not isinstance(time_slot, str) or not time_slot.strip():
            continue

        time_slot = time_slot.strip()

        for rel_idx, col_idx in enumerate(range(1, total_cols)):
            if rel_idx >= len(filled_days) or rel_idx >= len(venues):
                continue

            day = filled_days[rel_idx]
            venue = venues[rel_idx]
            cell_val = df.iloc[row_idx, col_idx]

            if pd.isna(cell_val):
                continue

            parsed_entries = parse_cell_courses(str(cell_val), default_group="ELECTIVE", is_first_year=False)

            for course_code, group_list in parsed_entries:
                records.append({
                    "day": day,
                    "time": time_slot,
                    "year": year,
                    "branch": "ELECTIVE",
                    "courseCode": course_code,
                    "courseName": lookup_course_name(course_code),
                    "group": group_list,
                    "venue": venue,
                    "type": "Elective",
                    "isElective": True,
                })

    return records


def parse_grid_block(
    df: pd.DataFrame,
    start_col: int,
    end_col: int,
    block_type: str,
    time_col: pd.Series,
    year: int,
    branch: str,
    is_elective: bool = False,
) -> list[dict]:
    records = []
    total_cols = df.shape[1]

    if start_col >= total_cols:
        return records

    actual_end_col = min(end_col, total_cols - 1)

    raw_days = df.iloc[1, start_col : actual_end_col + 1].tolist()
    filled_days = []
    last_day = None
    for d in raw_days:
        val = str(d).strip() if pd.notna(d) and str(d).strip() else None
        if val and any(day in val for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]):
            last_day = val
        filled_days.append(last_day)

    raw_venues = df.iloc[2, start_col : actual_end_col + 1].tolist()
    venues = [
        str(v).strip() if pd.notna(v) and str(v).strip() and str(v).lower() != "nan" else block_type
        for v in raw_venues
    ]

    for row_idx in range(3, len(df)):
        time_slot = time_col.iloc[row_idx]
        if not isinstance(time_slot, str) or not time_slot.strip():
            continue

        time_slot = time_slot.strip()

        for rel_col_idx, abs_col_idx in enumerate(range(start_col, actual_end_col + 1)):
            if rel_col_idx >= len(filled_days) or rel_col_idx >= len(venues):
                continue

            day = filled_days[rel_col_idx]
            venue = venues[rel_col_idx]
            cell_val = df.iloc[row_idx, abs_col_idx]

            if pd.isna(cell_val):
                continue

            parsed_entries = parse_cell_courses(
                str(cell_val),
                default_group=branch,
                is_first_year=(year == 1),
            )
            for course_code, group_list in parsed_entries:
                records.append({
                    "day": day,
                    "time": time_slot,
                    "year": year,
                    "branch": branch,
                    "courseCode": course_code,
                    "courseName": lookup_course_name(course_code),
                    "group": group_list,
                    "venue": venue,
                    "type": block_type,
                    "isElective": is_elective,
                })

    return records


def parse_tutorial_block_year1(df: pd.DataFrame, tut_col_start: int) -> list[dict]:
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
                "branch": "ALL",
                "courseCode": course_code,
                "courseName": lookup_course_name(course_code),
                "group": [group_clean],
                "venue": venue_clean,
                "type": "Tutorial",
                "isElective": False,
            })

    return records


def parse_all_sections(
    csv_url_or_filepath: str,
    year: int = 1,
    branch: str = "ALL",
    is_elective: bool = False,
) -> list[dict]:
    if csv_url_or_filepath.startswith("http"):
        res = requests.get(csv_url_or_filepath, timeout=10)
        res.raise_for_status()
        df = pd.read_csv(io.StringIO(res.content.decode("utf-8")), header=None)
    else:
        df = pd.read_csv(csv_url_or_filepath, header=None)

    total_cols = df.shape[1]
    if total_cols < 2:
        return []

    # Dedicated branch for elective sheets
    if is_elective:
        return parse_elective_sheet(df, year=year)

    # Standard branch sheets
    row_0 = df.iloc[0].fillna("").astype(str).tolist()

    lec_start = 1
    lab_start = -1
    tut_start = -1

    for idx, val in enumerate(row_0):
        val_upper = val.upper().strip()
        if "LAB" in val_upper and lab_start == -1:
            lab_start = idx
        elif "TUTORIAL" in val_upper and tut_start == -1:
            tut_start = idx

    lec_end = (lab_start - 1) if lab_start != -1 else total_cols - 1
    lab_end = (tut_start - 1) if tut_start != -1 else total_cols - 1
    tut_end = total_cols - 1

    time_col = df.iloc[:, 0]
    raw_records = []

    # 1. Lectures
    if lec_start < total_cols:
        raw_records.extend(
            parse_grid_block(df, lec_start, lec_end, "Lecture", time_col, year, branch, is_elective)
        )

    # 2. Labs
    if lab_start != -1 and lab_start < total_cols:
        raw_records.extend(
            parse_grid_block(df, lab_start, lab_end, "Lab", time_col, year, branch, is_elective)
        )

    # 3. Tutorials
    if tut_start != -1 and tut_start < total_cols:
        if year == 1:
            raw_records.extend(parse_tutorial_block_year1(df, tut_start))
        else:
            raw_records.extend(
                parse_grid_block(df, tut_start, tut_end, "Tutorial", time_col, year, branch, is_elective)
            )

    return merge_consecutive_lab_slots(raw_records)


def sync_sheet_to_mongo():
    base_sheet_id = os.getenv("SHEET_ID") or os.getenv("GOOGLE_SHEET_ID") or sheet_id
    all_records = []

    for config in SHEET_CONFIGS:
        gid = config["gid"]
        year = config["year"]
        branch = config["branch"]
        is_elective = config.get("is_elective", False)
        csv_url = f"https://docs.google.com/spreadsheets/d/{base_sheet_id}/export?format=csv&gid={gid}"

        try:
            records = parse_all_sections(csv_url, year=year, branch=branch, is_elective=is_elective)
            all_records.extend(records)
            print(f"✅ Parsed {len(records)} records for Year {year} ({branch}) [GID: {gid}]")
        except Exception as e:
            print(f"❌ Error parsing Year {year} ({branch}) [GID: {gid}]: {e}")

    if not all_records:
        print("⚠️ No records parsed. Skipping database update to protect existing data.")
        return 0

    col = get_mongo_collection()
    col.delete_many({})
    col.insert_many(all_records)
    col.create_index([("group", 1), ("year", 1), ("branch", 1), ("day", 1)])

    return len(all_records)


if __name__ == "__main__":
    synced = sync_sheet_to_mongo()
    print(f"🎉 Successfully synced {synced} total records with course names into MongoDB Atlas.")