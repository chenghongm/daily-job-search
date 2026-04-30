#!/usr/bin/env python3
"""
Daily Job Search Agent
- Fetches jobs from Adzuna API
- Scores and categorizes using Claude API
- Writes results to Google Sheets
- Marks completion on Google Calendar
"""

import os
import json
import datetime
import requests
from anthropic import Anthropic

# ── Google Sheets + Calendar ───────────────────────────────────
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# ── Config ─────────────────────────────────────────────────────
ADZUNA_APP_ID   = os.environ["ADZUNA_APP_ID"]
ADZUNA_APP_KEY  = os.environ["ADZUNA_APP_KEY"]
ANTHROPIC_KEY   = os.environ["ANTHROPIC_API_KEY"]
SPREADSHEET_ID  = os.environ["SPREADSHEET_ID"]

GOOGLE_CREDS    = os.environ["GOOGLE_CREDENTIALS"]    # full JSON string

RESUME_PROFILE  = json.load(open("resume_profile.json"))
TODAY           = datetime.date.today().isoformat()

# ── Adzuna Search ──────────────────────────────────────────────
SEARCH_QUERIES = [
    # safe 80%
    {"what": "full stack developer react laravel",  "gradient": "80% Safe"},
    {"what": "full stack developer react php",      "gradient": "80% Safe"},
    # stretch 60%
    {"what": "senior full stack AI engineer",       "gradient": "60% Stretch"},
    {"what": "LLM engineer full stack",             "gradient": "60% Stretch"},
    # reach 40%
    {"what": "staff engineer AI platform",          "gradient": "40% Reach"},
    {"what": "AI infrastructure engineer",          "gradient": "40% Reach"},
]

ADZUNA_BASE = "https://api.adzuna.com/v1/api/jobs/us/search/1"

def fetch_jobs_for_query(query: dict) -> list:
    params = {
        "app_id":           ADZUNA_APP_ID,
        "app_key":          ADZUNA_APP_KEY,
        "results_per_page": 5,
        "what":             query["what"],
        "where":            "San Francisco Bay Area",
        "distance":         50,
        "sort_by":          "date",
        "max_days_old":     2,
        "content-type":     "application/json",
    }
    try:
        resp = requests.get(ADZUNA_BASE, params=params, timeout=15)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        for r in results:
            r["_gradient"] = query["gradient"]
        return results
    except Exception as e:
        print(f"Adzuna error for '{query['what']}': {e}")
        return []


def fetch_remote_jobs(query: dict) -> list:
    """Also search remote jobs (no location constraint)."""
    params = {
        "app_id":           ADZUNA_APP_ID,
        "app_key":          ADZUNA_APP_KEY,
        "results_per_page": 3,
        "what":             query["what"] + " remote",
        "sort_by":          "date",
        "max_days_old":     2,
        "content-type":     "application/json",
    }
    try:
        resp = requests.get(ADZUNA_BASE, params=params, timeout=15)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        for r in results:
            r["_gradient"]  = query["gradient"]
            r["_is_remote"] = True
        return results
    except Exception as e:
        print(f"Adzuna remote error for '{query['what']}': {e}")
        return []


def collect_all_jobs() -> list:
    seen_ids = set()
    all_jobs = []
    for q in SEARCH_QUERIES:
        for job in fetch_jobs_for_query(q) + fetch_remote_jobs(q):
            jid = job.get("id", "")
            if jid and jid not in seen_ids:
                seen_ids.add(jid)
                all_jobs.append(job)
    return all_jobs


# ── Claude Scoring ─────────────────────────────────────────────
def score_jobs_with_claude(jobs: list) -> list:
    client = Anthropic(api_key=ANTHROPIC_KEY)

    job_list_text = "\n\n".join([
        f"[{i+1}] Title: {j.get('title','')}\n"
        f"    Company: {j.get('company',{}).get('display_name','')}\n"
        f"    Location: {j.get('location',{}).get('display_name','')}\n"
        f"    Gradient hint: {j.get('_gradient','')}\n"
        f"    Description snippet: {j.get('description','')[:300]}"
        for i, j in enumerate(jobs)
    ])

    profile_summary = json.dumps(RESUME_PROFILE["core_skills"], ensure_ascii=False)

    prompt = f"""You are a job matching assistant. Evaluate each job against this candidate profile.

CANDIDATE PROFILE:
- 5+ years Full Stack Developer (React, Laravel/PHP, MySQL, AWS, Docker)
- AI/LLM experience: prompt engineering, local fine-tuning, AI agent building
- Location: SF Bay Area, open to remote, NO relocation
- GitHub: 6200+ contributions

SKILLS: {profile_summary}

JOBS TO EVALUATE:
{job_list_text}

For each job return a JSON array. Each element:
{{
  "index": <1-based number>,
  "match_score": <0-100>,
  "gradient": "<40% Reach | 60% Stretch | 80% Safe>",
  "match_reason": "<1 sentence why>",
  "red_flags": "<any mismatch or concern, or 'none'>",
  "apply_recommendation": "<Yes | Maybe | Skip>"
}}

Return ONLY the JSON array, no markdown, no explanation."""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    try:
        scored = json.loads(response.content[0].text)
    except Exception as e:
        print(f"Claude parse error: {e}")
        scored = []

    # Merge scores back into job dicts
    score_map = {s["index"]: s for s in scored}
    enriched = []
    for i, job in enumerate(jobs):
        s = score_map.get(i + 1, {})
        job["_match_score"]          = s.get("match_score", 0)
        job["_gradient"]             = s.get("gradient", job.get("_gradient", ""))
        job["_match_reason"]         = s.get("match_reason", "")
        job["_red_flags"]            = s.get("red_flags", "")
        job["_apply_recommendation"] = s.get("apply_recommendation", "")
        enriched.append(job)

    # Sort by score desc, pick top 10
    enriched.sort(key=lambda x: x["_match_score"], reverse=True)
    return enriched[:10]


# ── Google Sheets ──────────────────────────────────────────────
SHEET_HEADERS = [
    "Date", "Gradient", "Score", "Recommend",
    "Job Title", "Company", "Location", "Remote",
    "URL", "Match Reason", "Red Flags", "Status", "Notes"
]

def get_google_creds():
    creds_dict = json.loads(GOOGLE_CREDS)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/calendar",
    ]
    return Credentials.from_service_account_info(creds_dict, scopes=scopes)


def get_sheet():
    creds = get_google_creds()
    gc    = gspread.authorize(creds)
    sh    = gc.open_by_key(SPREADSHEET_ID)

    try:
        ws = sh.worksheet("Jobs")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title="Jobs", rows=1000, cols=len(SHEET_HEADERS))
        ws.append_row(SHEET_HEADERS)
        # Freeze header row
        ws.freeze(rows=1)

    return ws


def write_jobs_to_sheet(jobs: list):
    ws = get_sheet()

    rows = []
    for j in jobs:
        redirect = j.get("redirect_url", "")
        is_remote = "Yes" if j.get("_is_remote") or "remote" in j.get("title","").lower() else ""
        rows.append([
            TODAY,
            j.get("_gradient", ""),
            j.get("_match_score", ""),
            j.get("_apply_recommendation", ""),
            j.get("title", ""),
            j.get("company", {}).get("display_name", ""),
            j.get("location", {}).get("display_name", ""),
            is_remote,
            redirect,
            j.get("_match_reason", ""),
            j.get("_red_flags", ""),
            "Pending",   # Status — update manually
            "",          # Notes
        ])

    ws.append_rows(rows, value_input_option="USER_ENTERED")
    print(f"✅ Written {len(rows)} jobs to Google Sheets")
    return rows


# ── Google Calendar ────────────────────────────────────────────
def mark_calendar(jobs: list):
    safe    = len([j for j in jobs if "80" in j.get("_gradient", "")])
    stretch = len([j for j in jobs if "60" in j.get("_gradient", "")])
    reach   = len([j for j in jobs if "40" in j.get("_gradient", "")])

    sheet_url = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}"

    creds   = get_google_creds()
    service = build("calendar", "v3", credentials=creds)

    # All-day event for today
    event = {
        "summary": f"🎯 Job Search: {len(jobs)} jobs found",
        "description": (
            f"✅ 80% Safe: {safe}\n"
            f"🟡 60% Stretch: {stretch}\n"
            f"🔴 40% Reach: {reach}\n\n"
            f"View Sheet → {sheet_url}"
        ),
        "start": {"date": TODAY},
        "end":   {"date": TODAY},
        "colorId": "2",   # green
    }

    service.events().insert(calendarId="primary", body=event).execute()
    print("✅ Calendar event created")


# ── Main ───────────────────────────────────────────────────────
def main():
    print(f"🔍 Starting daily job search — {TODAY}")

    print("📡 Fetching jobs from Adzuna...")
    raw_jobs = collect_all_jobs()
    print(f"   Found {len(raw_jobs)} raw listings")

    if not raw_jobs:
        print("⚠️  No jobs found today, exiting.")
        return

    print("🤖 Scoring with Claude...")
    scored_jobs = score_jobs_with_claude(raw_jobs)
    print(f"   Top {len(scored_jobs)} jobs selected")

    print("📊 Writing to Google Sheets...")
    write_jobs_to_sheet(scored_jobs)

    print("📅 Marking Google Calendar...")
    mark_calendar(scored_jobs)

    print("✅ Done!")


if __name__ == "__main__":
    main()
