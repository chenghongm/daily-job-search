#!/usr/bin/env python3
"""
Daily Job Search Agent
- Fetches jobs from Adzuna API
- Scores and categorizes using Claude API
- Writes results to Google Sheets
- Sends summary email via Resend
"""

import os
import json
import datetime
import requests
from anthropic import Anthropic

# ── Google Sheets ──────────────────────────────────────────────
import gspread
from google.oauth2.service_account import Credentials

# ── Config ─────────────────────────────────────────────────────
ADZUNA_APP_ID   = os.environ["ADZUNA_APP_ID"]
ADZUNA_APP_KEY  = os.environ["ADZUNA_APP_KEY"]
ANTHROPIC_KEY   = os.environ["ANTHROPIC_API_KEY"]
SPREADSHEET_ID  = os.environ["SPREADSHEET_ID"]
RESEND_API_KEY  = os.environ["RESEND_API_KEY"]
NOTIFY_EMAIL    = os.environ["NOTIFY_EMAIL"]          # your email address
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

def get_sheet():
    creds_dict = json.loads(GOOGLE_CREDS)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
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


# ── Resend Email Summary ───────────────────────────────────────
def send_email_summary(jobs: list):
    safe    = [j for j in jobs if "80" in j.get("_gradient","")]
    stretch = [j for j in jobs if "60" in j.get("_gradient","")]
    reach   = [j for j in jobs if "40" in j.get("_gradient","")]

    def job_block(j):
        url = j.get("redirect_url","#")
        return (
            f"<li><b>{j.get('title','')}</b> @ {j.get('company',{}).get('display_name','')}"
            f" &nbsp;|&nbsp; Score: {j.get('_match_score','')} "
            f"&nbsp;|&nbsp; {j.get('_apply_recommendation','')}<br>"
            f"<small>{j.get('_match_reason','')}</small><br>"
            f"<a href='{url}'>{url}</a></li>"
        )

    def section(title, color, job_list):
        if not job_list:
            return ""
        items = "".join(job_block(j) for j in job_list)
        return f"<h3 style='color:{color}'>{title}</h3><ul>{items}</ul>"

    html = f"""
    <html><body style='font-family:sans-serif;max-width:700px'>
    <h2>🎯 Daily Job Digest — {TODAY}</h2>
    <p>Found <b>{len(jobs)}</b> matched positions today. 
    <a href='https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}'>View full sheet →</a></p>
    {section("✅ 80% Safe — High Match", "#2e7d32", safe)}
    {section("🟡 60% Stretch — Good Challenge", "#f57f17", stretch)}
    {section("🔴 40% Reach — Ambitious", "#c62828", reach)}
    <hr><p style='color:#999;font-size:12px'>daily-job-search agent · GitHub Actions</p>
    </body></html>
    """

    payload = {
        "from":    "Job Agent <onboarding@resend.dev>",
        "to":      [NOTIFY_EMAIL],
        "subject": f"🎯 {len(jobs)} Jobs Found — {TODAY}",
        "html":    html,
    }
    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type":  "application/json",
    }
    resp = requests.post("https://api.resend.com/emails", json=payload, headers=headers, timeout=15)
    if resp.status_code in (200, 201):
        print("✅ Email sent")
    else:
        print(f"❌ Email failed: {resp.status_code} {resp.text}")


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

    print("📧 Sending email summary...")
    send_email_summary(scored_jobs)

    print("✅ Done!")


if __name__ == "__main__":
    main()
