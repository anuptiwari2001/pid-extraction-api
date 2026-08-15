# 🚀 GitHub Push: Quick Reference Card

## One-Liner Push

```powershell
cd "C:\Users\Anupam Tiwari\Documents\pid-extraction-api"; git remote add origin https://github.com/YOUR_USERNAME/pid-extraction-api.git; git branch -M main; git push -u origin main
```

---

## Step-by-Step (5 Minutes)

### 1️⃣ Open PowerShell
```powershell
cd "C:\Users\Anupam Tiwari\Documents\pid-extraction-api"
```

### 2️⃣ Create GitHub Repository
- Go to https://github.com/new
- Repository name: `pid-extraction-api`
- Click "Create repository"
- **Do NOT** initialize with README/gitignore

### 3️⃣ Connect to GitHub
```powershell
git remote add origin https://github.com/YOUR_USERNAME/pid-extraction-api.git
```

### 4️⃣ Rename Branch
```powershell
git branch -M main
```

### 5️⃣ Push
```powershell
git push -u origin main
```

### 6️⃣ Verify
Visit: https://github.com/YOUR_USERNAME/pid-extraction-api

---

## What Gets Pushed

| Item | Count |
|------|-------|
| Files | 62 |
| Changes | +7,469 insertions |
| Commit Message | "fix: increase Ollama timeout and add resilience..." |
| Documentation | 3 new guides |

**Key Files:**
- `.env` (OLLAMA_TIMEOUT_SECONDS=1200)
- `app/services/vlm/ollama_client.py` (retry logic)
- `app/db/sql_repository.py` (connection pool)
- `app/main.py` (middleware)
- `app/services/extraction/orchestrator.py` (timing logs)
- `TIMEOUT_TUNING_GUIDE.md` (comprehensive guide)

---

## Common Issues & Fixes

### ❌ "fatal: remote origin already exists"
```powershell
git remote remove origin
git remote add origin https://github.com/YOUR_USERNAME/pid-extraction-api.git
```

### ❌ "Authentication failed"
**For HTTPS:** Use Personal Access Token (not password)  
Create: https://github.com/settings/tokens/new

**For SSH:** Add key to https://github.com/settings/ssh/new

### ❌ "Updates were rejected..."
```powershell
# Pull remote changes first
git pull origin main
git push origin main
```

---

## Getting Help

| Need | Location |
|------|----------|
| **Detailed push steps** | Read `GITHUB_PUSH_GUIDE.md` |
| **Timeout configuration** | Read `TIMEOUT_TUNING_GUIDE.md` |
| **Quick summary** | Read `GITHUB_READY_SUMMARY.md` |
| **Git basics** | https://guides.github.com |
| **PAT creation** | https://github.com/settings/tokens/new |

---

## Commit Details

```
Commit Hash: 7d9b14a
Branch: main (was master, will rename on push)
Author: Anupam Tiwari

Message:
fix: increase Ollama timeout and add resilience for P&ID extraction

- Increase OLLAMA_TIMEOUT_SECONDS from 600 to 1200 seconds (20 min)
- Add dynamic per-request timeout override to ollama_client.py
- Add exponential backoff retry logic (2 retries) for transient failures
- Add explicit database connection pool timeouts to SQLRepository
- Add SlowRequestLoggingMiddleware to track >30 second requests
- Add detailed stage-by-stage extraction timing logs
- Create comprehensive TIMEOUT_TUNING_GUIDE.md
```

---

## Files Modified

```
✏️ .env
✏️ app/services/vlm/ollama_client.py
✏️ app/db/sql_repository.py
✏️ app/main.py
✏️ app/services/extraction/orchestrator.py
✨ TIMEOUT_TUNING_GUIDE.md
✨ .gitignore
```

---

## ✅ Verification Checklist

After pushing, you should see on GitHub:

- [ ] Repository: `https://github.com/YOUR_USERNAME/pid-extraction-api`
- [ ] 62 files visible
- [ ] Commit: "fix: increase Ollama timeout..."
- [ ] Branch: `main`
- [ ] `.gitignore` present
- [ ] `TIMEOUT_TUNING_GUIDE.md` present
- [ ] `app/` directory with all subdirectories
- [ ] `.env` file visible

---

## Next Steps

1. **Update README** with timeout tuning section
2. **Add .gitignore** to ignore build artifacts (already done ✓)
3. **Create Release** (optional): https://github.com/YOUR_USERNAME/pid-extraction-api/releases/new
4. **Add Collaborators** (if team): Settings → Collaborators
5. **Enable GitHub Actions** (optional): CI/CD testing on push

---

## Reference: Branch Rename

If you want to rename `master` to `main` locally:

```powershell
# Check current branch
git branch

# Rename
git branch -M main

# Verify
git branch

# Push with upstream tracking
git push -u origin main
```

---

## PowerShell Tips

**Copy command to clipboard:**
```powershell
@"
git remote add origin https://github.com/YOUR_USERNAME/pid-extraction-api.git
"@ | Set-Clipboard
```

**Execute entire push sequence:**
```powershell
$user = "YOUR_USERNAME"
git remote add origin https://github.com/$user/pid-extraction-api.git
git branch -M main
git push -u origin main
```

---

## Authentication: Quick Comparison

| Method | Setup Time | Security | Ease | Recommended |
|--------|-----------|----------|------|-------------|
| HTTPS + PAT | 2 min | High | Easy | ✅ Yes |
| SSH | 5 min | Very High | Medium | For teams |
| GitHub CLI | 3 min | High | Easy | Alternative |

---

**Your code is ready!** 🎉 Push now with confidence.

See **GITHUB_PUSH_GUIDE.md** for detailed instructions.
