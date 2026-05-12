# Daily Job Search Agent

Automated daily job search pipeline that fetches listings, evaluates fit across three LLMs, and logs results to Google Sheets.

## How It Works

Runs every weekday at 12am PST via GitHub Actions:
1. Fetches job listings from Adzuna API based on skills profile
2. Evaluates each listing in parallel using Claude, Gemini, and GPT
3. Each model scores fit (0–100), recommends action (Yes/Maybe/Skip), and explains reasoning
4. Results written to Google Sheets (separate tab per model) + email notification

## Why Three Models?

Beyond being a practical job search tool, running identical inputs through Claude, Gemini, and GPT simultaneously reveals how different models weigh the same criteria in its own way — useful as a lightweight research signal on cross-model judgment differences.

## File Structure

```
daily-job-search/
├── .github/workflows/daily_job_search.yml   # GitHub Actions schedule
├── job_search.py                            # main logic
├── resume_profile.json                      # skills profile
├── requirements.txt
└── README.md
```

## Setup

### 1. Adzuna API (free)
1. Register at https://developer.adzuna.com/
2. Create an app to get your `App ID` and `App Key`

### 2. Google Service Account
1. Open [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project
3. Enable **Google Sheets API** under APIs & Services
4. Create a Service Account with Editor role
5. Generate a JSON key and save the contents

### 3. Google Sheet
1. Create a new sheet at https://sheets.google.com
2. Share it with the Service Account email (`client_email` in the JSON) with Editor access
3. Copy the Sheet ID from the URL

### 4. GitHub Secrets
Go to repo → Settings → Secrets and variables → Actions → New repository secret

| Secret | Value |
|--------|-------|
| `ADZUNA_APP_ID` | Adzuna App ID |
| `ADZUNA_APP_KEY` | Adzuna App Key |
| `ANTHROPIC_API_KEY` | Anthropic API Key |
| `SPREADSHEET_ID` | Google Sheet ID |
| `RESEND_API_KEY` | Resend API Key |
| `NOTIFY_EMAIL` | Notification email address |
| `GOOGLE_CREDENTIALS` | Full contents of Service Account JSON |

### 5. Push and Run
```bash
git add .
git commit -m "init daily job search agent"
git push
```

To test manually: Actions → Daily Job Search → Run workflow

## Google Sheet Schema

Each model writes to its own tab.

| Column | Description |
|--------|-------------|
| Date | Fetch date |
| Gradient | 40% Reach / 60% Stretch / 80% Safe |
| Score | Match score 0–100 |
| Recommend | Yes / Maybe / Skip |
| Job Title | Position title |
| Company | Company name |
| Location | Location |
| Remote | Remote availability |
| URL | Job listing URL |
| Match Reason | Why this role fits |
| Red Flags | Mismatches or concerns |
| **Status** | Manual update: Pending / Applied / Interview / Offer / Rejected |
| Notes | Additional notes |

## Updating Your Skills Profile
Edit `resume_profile.json` and push — takes effect on the next run.

## Timezone Note
Cron in `.github/workflows/daily_job_search.yml`:
```
0 18 * * 1-5   # PDT (UTC-7) = 11am
0 19 * * 1-5   # PST (UTC-8) = 11am — use this after November DST change
```


# daily-job-search 

每天 12am PST 自动搜职位 → Claude/Gemini/GPT 对比，评分，并写原因 → Google Sheets → marked at calendar

---

## 文件结构

```
daily-job-search/
├── .github/workflows/daily_job_search.yml   # GitHub Actions schedule
├── job_search.py                            # main logic
├── resume_profile.json                      # skills
├── requirements.txt
└── README.md
```

---

## 一次性配置步骤

### 1. Adzuna API（免费）

1. 注册：https://developer.adzuna.com/
2. 创建 App，获得 `App ID` 和 `App Key`

---

### 2. Google Service Account

1. 打开 [Google Cloud Console](https://console.cloud.google.com/)
2. 新建项目（随便起名）
3. 左侧菜单 → **API 和服务** → **启用 API**
   - 搜索 `Google Sheets API` → 启用
4. 左侧 → **IAM 和管理** → **服务账号** → **创建服务账号**
   - 名字随便，角色选 `编辑者`
5. 创建完成后点进去 → **密钥** → **添加密钥** → **JSON**
6. 下载 JSON 文件，内容备用（后面放到 GitHub Secrets）

---

### 3. Google Sheet

1. 新建一个 Google Sheet：https://sheets.google.com
2. 把上面 Service Account 的邮箱（JSON 里的 `client_email`）**共享**给这个 Sheet（编辑权限）
3. 复制 Sheet URL 里的 ID：
   ```
   https://docs.google.com/spreadsheets/d/【这一段就是ID】/edit
   ```

---

### 4. GitHub Secrets 配置

进入repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

逐一添加以下 7 个：

| Secret 名称 | 值 |
|-------------|-----|
| `ADZUNA_APP_ID` | Adzuna 的 App ID |
| `ADZUNA_APP_KEY` | Adzuna 的 App Key |
| `ANTHROPIC_API_KEY` | Anthropic API Key |
| `SPREADSHEET_ID` | Google Sheet 的 ID |
| `RESEND_API_KEY` | Resend 的 API Key |
| `NOTIFY_EMAIL` | 想接收邮件的地址 |
| `GOOGLE_CREDENTIALS` | Service Account JSON 文件的**完整内容**（整个粘贴进去） |

---

### 5. Push 代码

```bash
git add .
git commit -m "init daily job search agent"
git push
```

---

## 手动触发测试

Push 完成后：
- 进入 repo → **Actions** → **Daily Job Search** → **Run workflow**
- 看 logs 确认跑通

---

## 时区说明

`.github/workflows/daily_job_search.yml` 里的 cron：
```
0 18 * * 1-5   # PDT (夏令时 UTC-7) = 11am PST
```
冬令时（11月初起）改成：
```
0 19 * * 1-5   # PST (UTC-8) = 11am PST
```

---

## Google Sheet 列说明

each model write on their own tab

| 列 | 说明 |
|----|------|
| Date | 抓取日期 |
| Gradient | 40% Reach / 60% Stretch / 80% Safe |
| Score | Claude 匹配分 0–100 |
| Recommend | Yes / Maybe / Skip |
| Job Title | 职位名 |
| Company | 公司名 |
| Location | 地点 |
| Remote | 是否 remote |
| URL | 职位链接 |
| Match Reason | Claude 匹配理由 |
| Red Flags | 不匹配的点 |
| **Status** | **手动更新：Pending / Applied / Interview / Offer / Rejected** |
| Notes | 备注 |

---

## 更新技能画像

编辑 `resume_profile.json`，commit push 即可，下次执行自动生效。
