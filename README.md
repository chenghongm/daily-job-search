# daily-job-search

每天 11am PST 自动搜职位 → Claude 评分 → Google Sheets → marked at calendar

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
