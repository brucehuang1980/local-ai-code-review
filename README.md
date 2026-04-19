# AI Code Review

使用地端 OpenAI-compatible LLM 審查 git commit range 的代碼審查工具。
支援手動 CLI 執行與 Bitbucket Server webhook 自動觸發。

## 安裝

```bash
pip install -r requirements.txt
cp .env.example .env
# 編輯 .env 填入你的 LLM endpoint 與 Bitbucket 設定
```

## 設定

### `.env`（必填）

| 變數 | 說明 |
|---|---|
| `LLM_BASE_URL` | 地端 LLM endpoint，如 `http://192.168.1.100:8000/v1` |
| `LLM_API_KEY` | API Key（不需驗證可填任意值） |
| `LLM_MODEL` | Model name，如 `gpt-oss-120b` |

### `config.yaml`（選填）

| 欄位 | 預設 | 說明 |
|---|---|---|
| `review.language` | `zh-TW` | 報告語言 |
| `review.max_diff_lines` | `500` | 單次送給 LLM 的最大行數 |

## 使用方式

### 手動 CLI

```bash
# 基本用法
python review.py --from abc1234 --to def5678

# 輸出到檔案
python review.py --from abc1234 --to def5678 --output report.md

# 臨時覆蓋 LLM 設定
python review.py --from abc1234 --to def5678 \
  --base-url http://other-server:8000/v1 \
  --model gpt-oss-120b

# 常見用法：審查最近 3 個 commit
python review.py --from HEAD~3 --to HEAD
```

### Webhook 自動觸發（PR 建立時自動審查）

**1. 啟動 webhook server**

```bash
python webhook_server.py
# 預設監聽 0.0.0.0:8000
```

**2. 在 Bitbucket Server 設定 webhook**

進入 Repository Settings → Webhooks → Create webhook：

| 欄位 | 值 |
|---|---|
| URL | `http://<your-server>:8000/webhook` |
| Secret | 與 `.env` 的 `WEBHOOK_SECRET` 相同 |
| Events | Pull Request: Opened、Source branch updated |

**3. 建立 PR 後**，bot 會自動審查並將報告貼到 PR comment。

webhook server 需要的額外 `.env` 設定：

| 變數 | 說明 |
|---|---|
| `BITBUCKET_BASE_URL` | 如 `http://co-git` |
| `BITBUCKET_TOKEN` | Personal Access Token（需 repo 讀寫） |
| `WEBHOOK_SECRET` | 與 Bitbucket webhook 設定相同的 secret |
| `WEBHOOK_PORT` | 監聽 port，預設 `8000` |
| `REPO_CLONE_DIR` | repo clone 暫存路徑，預設 `/tmp/code-review-repos` |

## 上傳至 Bitbucket

```bash
cd /path/to/this/repo
git init
git add .
git commit -m "feat: add AI code review tool"
git remote add origin http://co-git/scm/<PROJECT>/<REPO>.git
git push -u origin main
```

## 參數優先順序

CLI 參數 > `.env` > `config.yaml` 預設值
