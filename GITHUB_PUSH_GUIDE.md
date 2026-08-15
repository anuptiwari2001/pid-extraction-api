# 🚀 GitHub Push Guide: Ollama Timeout Fix

Your P&ID Extraction API timeout fixes are ready to push to GitHub!

## ✅ What's Committed

**Commit Hash:** `7d9b14a`  
**Commit Message:**
```
fix: increase Ollama timeout and add resilience for P&ID extraction

- Increase OLLAMA_TIMEOUT_SECONDS from 600 to 1200 seconds (20 min)
- Add dynamic per-request timeout override
- Add exponential backoff retry logic (2 retries)
- Add explicit database connection pool timeouts
- Add request logging middleware
- Add detailed extraction stage timing logs
- Create comprehensive TIMEOUT_TUNING_GUIDE.md
```

**Files Modified:**
- ✏️ `.env` — OLLAMA_TIMEOUT_SECONDS=1200
- ✏️ `app/services/vlm/ollama_client.py` — retry logic + dynamic timeout
- ✏️ `app/db/sql_repository.py` — connection pool configuration
- ✏️ `app/main.py` — request logging middleware
- ✏️ `app/services/extraction/orchestrator.py` — stage timing logs
- ✨ `TIMEOUT_TUNING_GUIDE.md` — new comprehensive tuning guide
- ✨ `.gitignore` — new git ignore file

---

## 🔗 Connect to GitHub Repository

### Step 1: Create Remote and Push

If you already have a GitHub repo:

```bash
cd C:\Users\Anupam Tiwari\Documents\pid-extraction-api

# Add your GitHub repository as origin
git remote add origin https://github.com/YOUR_USERNAME/pid-extraction-api.git

# Verify remote
git remote -v

# Push to main branch (create it if needed)
git branch -M main
git push -u origin main
```

### Step 2: If You Need to Create a GitHub Repo First

1. Go to **https://github.com/new**
2. **Repository name:** `pid-extraction-api`
3. **Description:** "P&ID Extraction API with VLM/Vision analysis, symbol detection, and relationship inference"
4. **Visibility:** Public or Private (your choice)
5. **Do NOT initialize** with README, .gitignore, or license (use ours)
6. Click **"Create repository"**

Then run:
```bash
cd C:\Users\Anupam Tiwari\Documents\pid-extraction-api

git remote add origin https://github.com/YOUR_USERNAME/pid-extraction-api.git
git branch -M main
git push -u origin main
```

---

## 📋 Pre-Push Checklist

Before pushing, verify everything is ready:

```bash
# Check git log
git log --oneline
# Output should show: 7d9b14a fix: increase Ollama timeout...

# Check remote is configured
git remote -v
# Output should show: origin  https://github.com/YOUR_USERNAME/pid-extraction-api.git

# Verify staged changes
git status
# Output should show: "On branch main, nothing to commit"

# List all files to be pushed
git ls-files | head -20
```

---

## 🔐 Authentication

### Option A: HTTPS with Token (Recommended for Windows)

1. **Generate GitHub Personal Access Token (PAT):**
   - Go to **https://github.com/settings/tokens/new**
   - Select scopes: ✓ `repo` (full repo access)
   - Click **"Generate token"** and copy it (you'll only see it once!)

2. **Push with token:**
   ```bash
   git push -u origin main
   # When prompted for password, paste your PAT (not your actual password)
   ```

3. **Save credentials (optional, Windows):**
   ```bash
   git config --global credential.helper manager
   # Next push will save your PAT in Windows Credential Manager
   ```

### Option B: SSH (Advanced)

1. **Generate SSH key** (if you don't have one):
   ```bash
   ssh-keygen -t ed25519 -C "your_email@example.com"
   # Press Enter to save to default location
   # Press Enter to skip passphrase (or enter one for security)
   ```

2. **Add SSH key to GitHub:**
   - Copy your public key: `Get-Content -Path $env:USERPROFILE\.ssh\id_ed25519.pub`
   - Go to **https://github.com/settings/ssh/new**
   - Paste and save

3. **Use SSH URL:**
   ```bash
   git remote remove origin
   git remote add origin git@github.com:YOUR_USERNAME/pid-extraction-api.git
   git push -u origin main
   ```

---

## 📤 Push Commands

### Quick Push (One Command)

```bash
cd C:\Users\Anupam Tiwari\Documents\pid-extraction-api
git push -u origin main
```

### Verbose Push (See Details)

```bash
git push -u origin main -v
```

### Push Without Setting Upstream (if branch exists)

```bash
git push origin main
```

---

## ✨ What Gets Pushed

```
62 files changed, 7469 insertions(+)

Key files:
- .env (with OLLAMA_TIMEOUT_SECONDS=1200)
- .gitignore (Python/IDE ignores)
- TIMEOUT_TUNING_GUIDE.md (new comprehensive guide)
- app/services/vlm/ollama_client.py (retry logic)
- app/db/sql_repository.py (connection timeouts)
- app/main.py (logging middleware)
- app/services/extraction/orchestrator.py (stage timing)
- Full app/ directory structure
- Migrations and configurations
```

---

## 🎯 After Pushing

### 1. Verify on GitHub

```bash
# Open your repository
https://github.com/YOUR_USERNAME/pid-extraction-api
```

You should see:
- ✓ All 62 files visible
- ✓ Commit message: "fix: increase Ollama timeout..."
- ✓ Branch: `main`

### 2. Create a Pull Request (if working with team)

If you're working with collaborators:
1. Create a feature branch: `git checkout -b fix/timeout-resilience`
2. Push the branch: `git push -u origin fix/timeout-resilience`
3. Go to GitHub and create a Pull Request
4. Request review from teammates

### 3. Add Release Notes (Optional)

Go to **https://github.com/YOUR_USERNAME/pid-extraction-api/releases/new**

**Tag version:** `v1.0.0-timeout-fix`  
**Release title:** `Ollama Timeout Resilience & Observability`  
**Description:**
```markdown
## What's New

### 🐛 Fixed
- **Ollama timeout errors** on dense P&ID PDFs (600s → 1200s)
- **Transient network failures** with exponential backoff retries
- **Database connection hangs** with explicit pool timeouts

### ✨ Added
- Dynamic per-request timeout override
- Detailed extraction stage timing logs
- Request logging middleware (30+ second requests)
- Comprehensive TIMEOUT_TUNING_GUIDE.md with:
  - Timeout setting explanations
  - Diagnosis strategies
  - Common scenarios and fixes
  - GPU offload troubleshooting

### 📊 Observability Improvements
- Logs now show which extraction stage is slow (CV, VLM, DB, structuring)
- Middleware tracks requests exceeding 30 seconds
- Retry attempts logged for visibility

###  🔧 Configuration Tuning
For dense P&IDs on slow hardware:
```bash
OLLAMA_TIMEOUT_SECONDS=1800  # 30 minutes
OLLAMA_NUM_GPU=999           # Max GPU offload
OLLAMA_NUM_CTX=12288         # Sufficient window
```

See `TIMEOUT_TUNING_GUIDE.md` for full details.
```

---

## 🛠 Troubleshooting Push Issues

### Issue: "fatal: remote origin already exists"

```bash
# Remove old remote
git remote remove origin

# Add new one
git remote add origin https://github.com/YOUR_USERNAME/pid-extraction-api.git
git push -u origin main
```

### Issue: "Authentication failed"

- **HTTPS + Token:** Make sure you're using the **token** (not your password)
- **SSH:** Check SSH key is added to GitHub: `ssh -T git@github.com`
- **Windows Credential Manager:** Clear old credentials: Settings → Credential Manager → Remove GitHub entry

### Issue: "Updates were rejected because the tip of your current branch is behind"

```bash
# This means the repo already has commits. Either:

# Option 1: Pull first (if you want to preserve remote changes)
git pull origin main
git push origin main

# Option 2: Force push (if you're sure your version is correct)
git push -f origin main
```

### Issue: "LF will be replaced by CRLF" Warnings

This is normal on Windows. To suppress:
```bash
git config core.autoCRLF true
```

---

## 📚 Post-Push Documentation

After pushing, update your GitHub repo documentation:

### 1. Add to README.md

```markdown
## Installation

### Environment Setup

1. Clone the repository:
```bash
git clone https://github.com/YOUR_USERNAME/pid-extraction-api.git
cd pid-extraction-api
```

2. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# OR
venv\Scripts\activate  # Windows
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Set up environment variables:
```bash
cp .env.example .env
# Edit .env with your settings
```

### Timeout Configuration

If you experience timeout errors during P&ID extraction, see [TIMEOUT_TUNING_GUIDE.md](TIMEOUT_TUNING_GUIDE.md) for:
- Default timeout values
- When to increase timeouts
- GPU vs. CPU performance tuning
- Troubleshooting GPU offload issues

Start with:
```bash
OLLAMA_TIMEOUT_SECONDS=1200   # 20 minutes (increased from 600s)
OLLAMA_NUM_GPU=999             # Enable GPU offload
```
```

### 2. Create CONTRIBUTING.md

```markdown
# Contributing

## Development Setup

```bash
# Fork and clone
git clone https://github.com/YOUR_USERNAME/pid-extraction-api.git
cd pid-extraction-api

# Create feature branch
git checkout -b feature/your-feature-name

# Make changes, test, commit
git commit -m "feat: add your feature"

# Push
git push origin feature/your-feature-name

# Create Pull Request on GitHub
```

## Testing

Before pushing:
```bash
# Python syntax check
python -m py_compile app/**/*.py

# Run extraction on a test PDF
curl -X POST http://localhost:8000/extract \
  -F "files=@test.pdf"

# Check logs
tail -f logs/pid_extraction.log
```

## Deployment

See TIMEOUT_TUNING_GUIDE.md for production environment tuning.
```

---

## ✅ Final Verification Checklist

- [ ] Git initialized in project directory
- [ ] Commit created with message: "fix: increase Ollama timeout..."
- [ ] 62 files staged
- [ ] GitHub repository created (or existing one verified)
- [ ] Remote URL configured: `git remote -v`
- [ ] Authentication ready (token or SSH key)
- [ ] `.gitignore` in place (ignores `.vs/`, `__pycache__/`, etc.)
- [ ] No sensitive data in `.env` (only example values)
- [ ] Ready to push: `git push -u origin main`

---

## 🚀 You're Ready!

Run this one command to push everything:

```bash
cd "C:\Users\Anupam Tiwari\Documents\pid-extraction-api"
git push -u origin main
```

Then verify on GitHub: **https://github.com/YOUR_USERNAME/pid-extraction-api**

---

## 📖 Additional Resources

- **Git Basics:** https://guides.github.com/introduction/git-handbook/
- **GitHub Authentication:** https://docs.github.com/en/authentication
- **Personal Access Token:** https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-personal-access-token
- **SSH Setup:** https://docs.github.com/en/authentication/connecting-to-github-with-ssh
- **PR Best Practices:** https://github.com/features/code-review/

---

**Questions?** Check the TIMEOUT_TUNING_GUIDE.md in your repo or GitHub documentation.

Good luck! 🎉
