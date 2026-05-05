#!/usr/bin/env python3
"""
Daily Job Search Agent
- Fetches jobs from Adzuna API + Greenhouse + Lever
- Scores and categorizes using AI (Claude / Gemini / GPT-4o, switchable)
- Writes results to Google Sheets
- Marks completion on Google Calendar

Set SCORING_MODEL in GitHub Secrets to switch:
  "claude"  → claude-sonnet-4-6      (default)
  "gemini"  → gemini-1.5-flash
  "gpt"     → gpt-4o
"""

import os
import json
import datetime
from pydoc import text
import requests
from anthropic import Anthropic

# ── Google Sheets + Calendar ───────────────────────────────────
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# ── Config ─────────────────────────────────────────────────────
ADZUNA_APP_ID   = os.environ["ADZUNA_APP_ID"]
ADZUNA_APP_KEY  = os.environ["ADZUNA_APP_KEY"]
ANTHROPIC_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
GEMINI_KEY      = os.environ.get("GEMINI_API_KEY", "")
OPENAI_KEY      = os.environ.get("OPENAI_API_KEY", "")
SPREADSHEET_ID  = os.environ["SPREADSHEET_ID"]
GOOGLE_CREDS    = os.environ["GOOGLE_CREDENTIALS"]
SCORING_MODEL   = os.environ.get("SCORING_MODEL", "claude").lower()  # claude | gemini | gpt

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
        "max_days_old":     14,
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
        "max_days_old":     14,
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

    # Add Greenhouse and Lever jobs
    for job in fetch_greenhouse_jobs() + fetch_lever_jobs():
        jid = job.get("id", "")
        if jid and jid not in seen_ids:
            seen_ids.add(jid)
            all_jobs.append(job)

    return all_jobs


# ── Greenhouse ─────────────────────────────────────────────────
# Well-known Bay Area / remote-friendly companies on Greenhouse
GREENHOUSE_BOARDS = [
    "anthropic", "openai", "stripe", "notion", "figma",
    "vercel", "linear", "retool", "rippling", "brex",
    "scale", "weights-biases", "cohere", "mistral",
]

KEYWORDS_FULLSTACK = ["full stack", "fullstack", "full-stack", "frontend", "backend", "software engineer", "react", "laravel"]

def fetch_greenhouse_jobs() -> list:
    jobs = []
    for board in GREENHOUSE_BOARDS:
        try:
            url  = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                continue
            for j in resp.json().get("jobs", []):
                title = j.get("title", "").lower()
                if not any(k in title for k in KEYWORDS_FULLSTACK):
                    continue
                location = j.get("location", {}).get("name", "")
                is_remote = "remote" in location.lower()
                is_bay    = any(x in location.lower() for x in ["san francisco", "bay area", "sf", "remote"])
                if not (is_remote or is_bay):
                    continue
                jobs.append({
                    "id":          f"gh-{j.get('id')}",
                    "title":       j.get("title", ""),
                    "company":     {"display_name": board.capitalize()},
                    "location":    {"display_name": location},
                    "redirect_url": j.get("absolute_url", ""),
                    "description": j.get("content", "")[:500],
                    "_gradient":   "60% Stretch",
                    "_is_remote":  is_remote,
                    "_source":     "Greenhouse",
                })
        except Exception as e:
            print(f"Greenhouse error for {board}: {e}")
    print(f"   Greenhouse: {len(jobs)} jobs")
    return jobs


# ── Lever ──────────────────────────────────────────────────────
LEVER_BOARDS = [
    "netflix", "airbnb", "lyft", "coinbase", "reddit",
    "discord", "airtable", "carta", "lattice", "loom",
    "benchling", "plaid", "asana",
]

def fetch_lever_jobs() -> list:
    jobs = []
    for board in LEVER_BOARDS:
        try:
            url  = f"https://api.lever.co/v0/postings/{board}?mode=json"
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                continue
            for j in resp.json():
                title    = j.get("text", "").lower()
                if not any(k in title for k in KEYWORDS_FULLSTACK):
                    continue
                location = j.get("categories", {}).get("location", "")
                team     = j.get("categories", {}).get("team", "")
                is_remote = "remote" in location.lower()
                is_bay    = any(x in location.lower() for x in ["san francisco", "bay area", "sf", "remote"])
                if not (is_remote or is_bay):
                    continue
                jobs.append({
                    "id":          f"lv-{j.get('id')}",
                    "title":       j.get("text", ""),
                    "company":     {"display_name": board.capitalize()},
                    "location":    {"display_name": location or "Remote"},
                    "redirect_url": j.get("hostedUrl", ""),
                    "description": j.get("descriptionPlain", "")[:500],
                    "_gradient":   "60% Stretch",
                    "_is_remote":  is_remote,
                    "_source":     "Lever",
                })
        except Exception as e:
            print(f"Lever error for {board}: {e}")
    print(f"   Lever: {len(jobs)} jobs")
    return jobs


# ── Scoring: build prompt ──────────────────────────────────────
def build_prompt(jobs: list) -> str:
    job_list_text = "\n\n".join([
        f"[{i+1}] Title: {j.get('title','')}\n"
        f"    Company: {j.get('company',{}).get('display_name','')}\n"
        f"    Location: {j.get('location',{}).get('display_name','')}\n"
        f"    Source: {j.get('_source','Adzuna')}\n"
        f"    Search bucket hint: {j.get('_gradient','')}\n"
        f"    Description: {j.get('description','')[:700]}"
        for i, j in enumerate(jobs)
    ])

    profile_summary = json.dumps(RESUME_PROFILE["core_skills"], ensure_ascii=False)

    # 保持同样的 f-string 结构，但优化内部逻辑
    return f"""You are a senior technical recruiter specializing in engineering roles.
Your goal is to evaluate if this specific candidate should apply based on high-impact results.

CANDIDATE CORE VALUE:
- 5+ years Full Stack Developer (PHP/Laravel, React, Java, MySQL)
- KEY ACHIEVEMENT: Optimized 12TB storage to 7TB (42% reduction) - indicates high-level data architecture skills.
- HIGH VELOCITY: 6200+ GitHub contributions (extreme output density).
- AI DEPTH: Built local Llama-3 fine-tuning pipelines (MLX) and AI UX tools.
- Location: SF Bay Area / Remote only. NO relocation.

SKILLS:
{profile_summary}

SCORING MEANING:
- 90-100: Exceptional fit; technical stack and seniority align perfectly.
- 80-89: Strong fit; highly realistic.
- 70-79: Good candidate; minor stack gap or "Safe Reach" for Staff roles.
- 60-69: Stretch; significant domain or seniority mismatch.
- <60: Skip.

STRICT CALIBRATION RULES:
1. SENIORITY FLEXIBILITY: The 12TB optimization and 6200+ commits represent "Staff-level impact". 
   - Senior roles are the "Sweet Spot" (85-95).
   - Staff roles: If the JD is hands-on and technical (Laravel/React/Data), LIFT the 75-point cap. Score 75-84.
   - ONLY score <=72 if the role is purely People Management or 12+ years leadership.
2. DATA OVER KEYWORDS: Prioritize jobs mentioning "Refactoring", "Optimization", or "Scale".
3. LOCATION: Hard red flag if relocation is required.

GRADIENT RULES:
- 80% Safe = Strong technical and seniority alignment.
- 60% Stretch = Good tech match but seniority/domain gap.
- 40% Reach = Staff/Principal level or tech stack pivot.

JOBS TO EVALUATE:
{job_list_text}

Return ONLY a JSON array. Each element:
{{
  "index": <number>,
  "match_score": <number>,
  "gradient": "<40% Reach | 60% Stretch | 80% Safe>",
  "technical_hook": "<1 sentence linking the candidate's specific achievement (e.g. 12TB optimization) to this JD>",
  "red_flags": "<seniority/location/domain mismatch, or 'none'>",
  "apply_recommendation": "<Yes | Maybe | Skip>"
}}

Recommendation rules:
- Yes: score >= 78 and no location red flag.
- Maybe: score 65-77, or high-fit Staff roles.
- Skip: score < 65 or relocation required.

Return ONLY the JSON array, no markdown, no explanation."""

def parse_json_response(text: str) -> list:
    try:
        clean = text.strip().replace("```json", "").replace("```", "").strip()
        return json.loads(clean)
    except Exception as e:
        print(f"JSON parse error: {e}")
        return []


# ── Scoring: Claude ────────────────────────────────────────────
def score_batch_claude(jobs: list) -> list:
    client   = Anthropic(api_key=ANTHROPIC_KEY)
    prompt   = build_prompt(jobs)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}]
    )
    text = response.content[0].text
    print(f"Response length: {len(text)}")
    print(f"Last 200 chars: {text[-200:]}")
    return parse_json_response(text)


# ── Scoring: Gemini ────────────────────────────────────────────
def score_batch_gemini(jobs: list) -> list:
    prompt = build_prompt(jobs)

    url    = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={GEMINI_KEY}"
    body   = {"contents": [{"parts": [{"text": prompt}]}]}
    resp   = requests.post(url, json=body, timeout=30)
    if not resp.ok:
        print(f"Gemini error: {resp.status_code} {resp.text}")
        resp.raise_for_status()
    # resp   = requests.post(url, json=body, timeout=30)
    # resp.raise_for_status()
    text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    return parse_json_response(text)


# ── Scoring: GPT-4o ────────────────────────────────────────────
def score_batch_gpt(jobs: list) -> list:
    prompt  = build_prompt(jobs)
    headers = {"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"}
    body    = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 2000,
    }
    resp = requests.post("https://api.openai.com/v1/chat/completions", json=body, headers=headers, timeout=30)
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"]
    return parse_json_response(text)


# ── Scoring: dispatcher ────────────────────────────────────────
def score_batch(jobs: list) -> list:
    print(f"   🤖 Scoring with: {SCORING_MODEL}")
    if SCORING_MODEL == "gemini":
        return score_batch_gemini(jobs)
    elif SCORING_MODEL == "gpt":
        return score_batch_gpt(jobs)
    else:
        return score_batch_claude(jobs)

def normalize_gradient(job: dict) -> str:
    title = job.get("title", "").lower()
    score = job.get("_match_score", 0)

    staff_terms = ["staff", "staff+", "senior staff", "principal", "architect"]
    is_staff_level = any(t in title for t in staff_terms)

    if is_staff_level:
        if "senior staff" in title or "principal" in title:
            return "40% Reach"
        if score > 75:
            job["_match_score"] = 75
        return "40% Reach"

    if score >= 80:
        return "80% Safe"
    if score >= 65:
        return "60% Stretch"
    return "40% Reach"

def pick_by_quota(scored: list) -> list:
    """Pick top jobs with gradient quota: 3 Safe + 5 Stretch + 2 Reach."""
    quotas = {"80% Safe": 3, "60% Stretch": 5, "40% Reach": 2}
    buckets = {"80% Safe": [], "60% Stretch": [], "40% Reach": []}
    print(f"   pick_by_quota received: {len(scored)} jobs")
    for job in sorted(scored, key=lambda x: x["_match_score"], reverse=True):
        gradient = job.get("_gradient", "")
        for key in buckets:
            if key in gradient and len(buckets[key]) < quotas[key]:
                buckets[key].append(job)
                break

    result = buckets["80% Safe"] + buckets["60% Stretch"] + buckets["40% Reach"]

    # If any bucket is short, fill with remaining high-score jobs
    if len(result) < 10:
        used_urls = {j.get("redirect_url") for j in result}
        extras = [j for j in sorted(scored, key=lambda x: x["_match_score"], reverse=True)
                  if j.get("redirect_url") not in used_urls]
        result += extras[:10 - len(result)]

    print(f"   Safe:{len(buckets['80% Safe'])} Stretch:{len(buckets['60% Stretch'])} Reach:{len(buckets['40% Reach'])}")
    return result


def score_jobs(jobs: list, model: str) -> list:
    """Score jobs with a specific model, return top 10 by gradient quota."""
    scored     = []
    batch_size = 15

    for i in range(0, len(jobs), batch_size):
        batch_copy = [dict(j) for j in jobs[i:i+batch_size]]
        print(f"   [{model}] batch {i//batch_size + 1}...")

        if model == "gemini":
            results = score_batch_gemini(batch_copy)
        elif model == "gpt":
            results = score_batch_gpt(batch_copy)
        else:
            results = score_batch_claude(batch_copy)

        score_map = {s["index"]: s for s in results}
        for idx, job in enumerate(batch_copy):
            s = score_map.get(idx + 1, {})
            job["_match_score"]          = s.get("match_score", 0)
            job["_gradient"]             = s.get("gradient", job.get("_gradient", ""))
            job["_match_reason"]         = s.get("technical_hook", "")
            job["_red_flags"]            = s.get("red_flags", "")
            job["_apply_recommendation"] = s.get("apply_recommendation", "")
            job["_gradient"] = normalize_gradient(job)
            job["_model"] = model # For later voting analysis
            if "Skip" in job.get("_apply_recommendation", ""): # Filter out Skip jobs early， to avoid filling the quota with low-quality listings
                continue
            scored.append(job)

    return pick_by_quota(scored)


# ── Google Sheets ──────────────────────────────────────────────
SHEET_HEADERS = [
    "Date", "Time", "Source", "Gradient", "Score", "Recommend",
    "Job Title", "Company", "Location", "Remote",
    "URL", "Match Reason", "Red Flags", "Status", "Notes"
]

def get_google_creds():
    creds_dict = json.loads(GOOGLE_CREDS)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/calendar",
    ]
    return Credentials.from_service_account_info(creds_dict, scopes=scopes)


def get_or_create_tab(sh, tab_name: str):
    try:
        ws = sh.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=tab_name, rows=1000, cols=len(SHEET_HEADERS))
        ws.append_row(SHEET_HEADERS)
        ws.freeze(rows=1)
    return ws


def write_jobs_to_tab(jobs: list, tab_name: str):
    creds = get_google_creds()
    gc    = gspread.authorize(creds)
    sh    = gc.open_by_key(SPREADSHEET_ID)
    ws    = get_or_create_tab(sh, tab_name)

    rows = []
    now = datetime.datetime.now().strftime("%H:%M:%S")
    for j in jobs:
        redirect  = j.get("redirect_url", "")
        is_remote = "Yes" if j.get("_is_remote") or "remote" in j.get("title","").lower() else ""
        rows.append([
            TODAY,
            now,
            j.get("_source", "Adzuna"),
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
            "Pending",
            "",
        ])

    ws.append_rows(rows, value_input_option="USER_ENTERED")

    # Color highlight Recommend column: Maybe=orange, Skip=red
    try:
        recommend_col = SHEET_HEADERS.index("Recommend") + 1
        all_values = ws.col_values(recommend_col)
        for row_idx, val in enumerate(all_values[1:], start=2):  # skip header
            if val == "Maybe":
                ws.format(f"{chr(64+recommend_col)}{row_idx}", {
                    "backgroundColor": {"red": 1.0, "green": 0.6, "blue": 0.0}
                })
            elif val == "Skip":
                ws.format(f"{chr(64+recommend_col)}{row_idx}", {
                    "backgroundColor": {"red": 0.9, "green": 0.2, "blue": 0.2}
                })
    except Exception as e:
        print(f"   Color format error (non-fatal): {e}")

    print(f"✅ [{tab_name}] Written {len(rows)} jobs")


# ── Google Calendar ────────────────────────────────────────────
def mark_calendar(results_by_model: dict):
    sheet_url = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}"
    creds     = get_google_creds()
    service   = build("calendar", "v3", credentials=creds)

    desc_lines = []
    total = 0
    for model, jobs in results_by_model.items():
        safe    = len([j for j in jobs if "80" in j.get("_gradient", "")])
        stretch = len([j for j in jobs if "60" in j.get("_gradient", "")])
        reach   = len([j for j in jobs if "40" in j.get("_gradient", "")])
        total  += len(jobs)
        desc_lines.append(f"[{model.upper()}] ✅{safe} 🟡{stretch} 🔴{reach}")

    event = {
        "summary": f"🎯 Job Search: {total} results ({len(results_by_model)} models)",
        "description": "\n".join(desc_lines) + f"\n\nView Sheet → {sheet_url}",
        "start": {"date": TODAY},
        "end":   {"date": TODAY},
        "colorId": "2",
    }

    calendar_id = os.environ.get("CALENDAR_ID", "primary")
    print(f"📅 Using calendar: {calendar_id}")
    result = service.events().insert(calendarId=calendar_id, body=event).execute()
    print(f"✅ Calendar event created: {result.get('htmlLink')}")


def extract_job_id(url: str) -> str:
    """Extract stable job ID from URL for dedup purposes."""
    import re
    if not url:
        return ""
    m = re.search(r'/land/ad/(\d+)', url)
    if m:
        return f"adzuna-{m.group(1)}"
    m = re.search(r'gh_jid=(\d+)', url)
    if m:
        return f"gh-{m.group(1)}"
    m = re.search(r'/jobs/(\d+)', url)
    if m:
        return f"gh-{m.group(1)}"
    m = re.search(r'/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', url)
    if m:
        return f"lv-{m.group(1)}"
    return url


# ── Dedup: read existing job IDs from all tabs ─────────────────
def get_seen_ids() -> set:
    """Read all job IDs already written to any tab in the Sheet."""
    try:
        creds   = get_google_creds()
        gc      = gspread.authorize(creds)
        sh      = gc.open_by_key(SPREADSHEET_ID)
        seen    = set()
        url_col = SHEET_HEADERS.index("URL") + 1
        for ws in sh.worksheets():
            try:
                urls = ws.col_values(url_col)[1:]
                for u in urls:
                    jid = extract_job_id(u)
                    if jid:
                        seen.add(jid)
            except Exception:
                pass
        print(f"   🔁 Dedup: {len(seen)} job IDs already seen")
        return seen
    except Exception as e:
        print(f"   Dedup read error (skipping): {e}")
        return set()

def filter_for_voting(results_by_model: dict) -> dict:
    """
    只保留值得投票的 jobs
    条件：
    - 至少一个模型标为 60% Stretch 或 80% Safe
    """

    job_map = {}

    for model, jobs in results_by_model.items():
        for j in jobs:
            url = j.get("redirect_url")
            if not url:
                continue

            if url not in job_map:
                job_map[url] = []

            job_map[url].append(j)

    filtered = {}

    for url, jobs in job_map.items():
        keep = any(
            ("60% Stretch" in j.get("_gradient", "") or
            "80% Safe" in j.get("_gradient", "") or
            j.get("_match_score", 0) >= 65)
            for j in jobs
        )

        if not keep:
            continue

        for j in jobs:
            model = j.get("_model")  # 如果你有标 model
            if model:
                filtered.setdefault(model, []).append(j)

    return filtered
def vote_result(results_by_model: dict) -> list:
 
    weights = {
        "gemini": 0.4,
        "claude": 0.35,
        "gpt": 0.25,
    }

    # collect by URL
    job_map = {}

    for model, jobs in results_by_model.items():
        for j in jobs:
            url = j.get("redirect_url")
            if not url:
                continue

            if url not in job_map:
                job_map[url] = {
                    "job": j,
                    "scores": {},
                    "recs": {}
                }

            job_map[url]["scores"][model] = j.get("_match_score", 0)
            job_map[url]["recs"][model] = j.get("_apply_recommendation", "")

    final_results = []

    for url, data in job_map.items():
        scores = data["scores"]
        recs   = data["recs"]

        # weighted score
        final_score = sum(
            weights[m] * scores.get(m, 0)
            for m in weights
        )

        # disagreement
        if scores:
            disagreement = max(scores.values()) - min(scores.values())
        else:
            disagreement = 0

        # vote counts
        yes_count  = sum(1 for r in recs.values() if r == "Yes")
        skip_count = sum(1 for r in recs.values() if r == "Skip")

        # final recommend
        if final_score >= 78 and disagreement <= 12:
            final_rec = "Strong Apply"
        elif final_score >= 68:
            final_rec = "Apply / Review"
        elif disagreement >= 18:
            final_rec = "Human Review"
        elif skip_count >= 2:
            final_rec = "Skip"
        else:
            final_rec = "Apply / Review"

        job = data["job"]

        job["_vote_score"] = round(final_score, 1)
        job["_vote_recommend"] = final_rec
        job["_disagreement"] = disagreement

        final_results.append(job)

    # sort
    final_results.sort(key=lambda x: x["_vote_score"], reverse=True)

    return final_results

def write_vote_results(jobs: list):
    creds = get_google_creds()
    gc    = gspread.authorize(creds)
    sh    = gc.open_by_key(SPREADSHEET_ID)

    ws = get_or_create_tab(sh, "Vote")

    rows = []
    now = datetime.datetime.now().strftime("%H:%M:%S")

    for j in jobs:
        rows.append([
            TODAY,
            now,
            j.get("_source", ""),
            j.get("_gradient", ""),
            j.get("_vote_score", ""),
            j.get("_vote_recommend", ""),   # 👈 关键
            j.get("title", ""),
            j.get("company", {}).get("display_name", ""),
            j.get("location", {}).get("display_name", ""),
            "Yes" if j.get("_is_remote") else "",
            j.get("redirect_url", ""),
            j.get("_match_reason", ""),
            f"disagreement={j.get('_disagreement', 0)}",
            "Pending",
            "",
        ])

    ws.append_rows(rows, value_input_option="USER_ENTERED")

    print(f"✅ [Vote] Written {len(rows)} jobs")

# ── Main ───────────────────────────────────────────────────────
def main():
    print(f"🔍 Starting daily job search — {TODAY}")

    print("📡 Fetching jobs from Adzuna + Greenhouse + Lever...")
    raw_jobs = collect_all_jobs()
    print(f"   Found {len(raw_jobs)} raw listings")

    if not raw_jobs:
        print("⚠️  No jobs found today, exiting.")
        return

    # Dedup against already-seen job IDs
    seen_ids = get_seen_ids()
    new_jobs  = [j for j in raw_jobs if extract_job_id(j.get("redirect_url", "")) not in seen_ids]
    print(f"   After dedup: {len(new_jobs)} new jobs")

    if not new_jobs:
        print("⚠️  No new jobs today, exiting.")
        return

    scoring_model = os.environ.get("SCORING_MODEL", "all").lower()
    models = ["claude", "gemini", "gpt"] if scoring_model == "all" else [scoring_model]

    results_by_model = {}
    for model in models:
        if model == "claude" and not ANTHROPIC_KEY:
            print(f"⚠️  Skipping claude: ANTHROPIC_API_KEY not set"); continue
        if model == "gemini" and not GEMINI_KEY:
            print(f"⚠️  Skipping gemini: GEMINI_API_KEY not set"); continue
        if model == "gpt" and not OPENAI_KEY:
            print(f"⚠️  Skipping gpt: OPENAI_API_KEY not set"); continue

        print(f"\n🤖 Scoring with {model.upper()}...")
        try:
            scored = score_jobs(new_jobs, model)
            print(f"   Top {len(scored)} jobs selected")
            write_jobs_to_tab(scored, model.capitalize())
            results_by_model[model] = scored
        except Exception as e:
            print(f"❌ {model} failed: {e}")

    if results_by_model:
        print("\n📅 Marking Google Calendar...")
        mark_calendar(results_by_model)

        # Perform voting logic
        print("\n🗳️ Voting across models...")

        filtered = filter_for_voting(results_by_model)

        if not filtered:
            print("⚠️ No high-value jobs for voting, skipping vote step")
        else:
            voted = vote_result(filtered)
            write_vote_results(voted)

    print("\n✅ Done!")


if __name__ == "__main__":
    main()