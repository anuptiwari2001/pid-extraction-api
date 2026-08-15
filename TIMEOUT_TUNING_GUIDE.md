# P&ID Extraction API: Timeout Tuning Guide

If you see **"Read timed out"** or **"Ollama timeout"** errors during PDF extraction, your hardware or P&ID complexity requires timeout adjustments. This guide explains the settings and how to tune them.

---

## Overview

The extraction pipeline has **three timeout layers**:

1. **Ollama VLM Inference Timeout** (`OLLAMA_TIMEOUT_SECONDS`)
   - How long to wait for the vision model to analyze an image
   - Default: **1200 seconds (20 minutes)**
   - Affects: Text extraction, symbol analysis with VLM

2. **Database Connection Timeout** (`SQL_CONNECT_TIMEOUT`)
   - How long to wait for a database connection to be acquired from the pool
   - Default: **30 seconds** (set in code)
   - Affects: Saving symbols, relationships, job status

3. **Request Logging** (SlowRequestLoggingMiddleware)
   - Logs requests exceeding **30 seconds** for visibility
   - Does NOT terminate requests (long extractions are normal)
   - Helps identify which stage is slow

---

## Timeout Settings

### Environment Variable: `OLLAMA_TIMEOUT_SECONDS`

Controls how long the API waits for Ollama to respond. Increase if you see timeout errors during text extraction or symbol analysis.

```bash
# .env
OLLAMA_TIMEOUT_SECONDS=1200  # Default: 20 minutes

# For CPU-only or very large PDFs (100+ symbols):
OLLAMA_TIMEOUT_SECONDS=1800  # 30 minutes

# For GPU with fast inference:
OLLAMA_TIMEOUT_SECONDS=600   # 10 minutes
```

**Why it might be slow:**
- **First request after cold start**: Ollama reloads model into VRAM (can take minutes)
- **Large num_ctx**: With `OLLAMA_NUM_CTX=12288`, the model allocates a large KV cache
- **Dense P&ID**: Complex drawings with many symbols generate longer prompts
- **CPU-only hardware**: Vision models run at ~2-3 tokens/sec on CPU vs. 20-40 tokens/sec on GPU
- **Insufficient VRAM**: Even with GPU, if `num_gpu=999` but insufficient VRAM, Ollama falls back to 100% CPU silently

### Environment Variable: `OLLAMA_NUM_GPU`

How many model layers to offload to GPU. Affects inference speed dramatically.

```bash
# .env
OLLAMA_NUM_GPU=999    # Default: Ask Ollama to offload as many layers as fit in VRAM

# Force pure CPU (for testing / low-VRAM systems):
OLLAMA_NUM_GPU=0      # All computation on CPU (slow, but predictable)
```

### Environment Variable: `OLLAMA_NUM_CTX`

Context window size (in tokens). Larger context = slower inference, but needed for dense P&IDs.

```bash
# .env
OLLAMA_NUM_CTX=12288  # Default: Must contain PROMPT + RESPONSE combined
					  # Observed: ~6000-token prompt + ~6000-token response buffer

# For simpler drawings (fewer symbols):
OLLAMA_NUM_CTX=8192   # Smaller context window = faster inference

# For very dense PDFs:
OLLAMA_NUM_CTX=16384  # Larger window, but slower
```

---

## Diagnosis: Which Stage is Timing Out?

The extraction pipeline logs each stage with timings. Check your logs (JSON or structured):

```
extraction_stage_timing: stage=symbol_detection, duration_seconds=5.2
extraction_stage_timing: stage=text_extraction, duration_seconds=45.8    ← VLM (Ollama) stage
extraction_stage_timing: stage=save_symbols, duration_seconds=1.1
extraction_stage_timing: stage=page_structuring, duration_seconds=3.4
```

- **symbol_detection → 5-60s**: CV model detects shapes (usually fast, GPU-accelerated)
- **text_extraction → 10-300s**: VLM reads text from symbols (THIS IS USUALLY SLOW)
  - If >60s: Increase `OLLAMA_TIMEOUT_SECONDS` and check GPU offload
- **save_symbols → 1-5s**: Database writes (if >10s, database may be slow)
- **page_structuring → 1-10s**: Classify symbols and infer relationships

If `text_extraction` dominates, **the Ollama timeout is your bottleneck**.

---

## Common Scenarios & Fixes

### Scenario 1: "Read timed out after 600 seconds" on first extraction

**Root cause**: First request after deploying or changing `OLLAMA_NUM_CTX` triggers a fresh model load.

**Fix**:
```bash
# In .env:
OLLAMA_TIMEOUT_SECONDS=1800  # 30 minutes for model reloading
```

### Scenario 2: Consistent timeout on dense P&IDs (50+ symbols per page)

**Root cause**: Large prompt + response tokens exceed default context or slow CPU inference.

**Fix** (in order):
1. **Check GPU offload** (terminal):
   ```bash
   ollama list  # Show models
   # Manually test: ollama run qwen3-vl:4b "what is this image?" < test.jpg
   # Look for: "evaluating layers..." — should say "N/(N+M) GPU"
   # If it says "evaluating layers 0/256 CPU", GPU offload isn't working
   ```

2. **If GPU offload is working**:
   ```bash
   # In .env:
   OLLAMA_TIMEOUT_SECONDS=1200  # Already set to 20 minutes
   OLLAMA_NUM_CTX=12288         # Sufficient for most dense P&IDs
   ```

3. **If GPU offload is NOT working** (shows 0/N GPU):
   - Force CPU-only and expect 20-60 seconds per page:
	 ```bash
	 OLLAMA_TIMEOUT_SECONDS=1800
	 OLLAMA_NUM_GPU=0
	 ```
   - OR troubleshoot GPU (run `nvidia-smi` or check Ollama docs for hybrid graphics issues)

### Scenario 3: "Ollama returned HTTP 400: exceeds the available context size"

**Root cause**: Prompt is larger than `OLLAMA_NUM_CTX`, model truncates and fails.

**Fix**:
```bash
# In .env, increase context window:
OLLAMA_NUM_CTX=16384  # Was 12288
```

Then restart API and retry. If still happening on same P&ID, the drawing is too complex for the model.

### Scenario 4: Database connection timeouts ("could not acquire connection")

**Root cause**: Rare, but happens if database is overloaded or unreachable.

**Fix** (in code, not env):
- Already set to `pool_timeout=30` and `connect_timeout=30` seconds
- If database is very slow, check:
  - Is database server running? (`ping <db-host>`)
  - Are there connection pool exhaustion issues? (check DB logs for "too many connections")
  - Is network latency high? (run `psql -h <host> -U <user> -d <db> -c "SELECT 1"`)

---

## Best Practices

1. **Monitor logs during extraction**:
   ```bash
   # Terminal 1: Start API with debug logging
   LOG_LEVEL=DEBUG python -m uvicorn app.main:app --reload

   # Terminal 2: Post extraction job
   curl -X POST http://localhost:8000/extract \
	 -F "files=@large.pdf" \
	 -F "project_id=" \
	 -F "confidence_threshold=0.75"

   # Terminal 1: Watch logs for stage timings
   extraction_stage_timing: stage=text_extraction, duration_seconds=120.5
   ```

2. **For first deployment or model config change**:
   - Set `OLLAMA_TIMEOUT_SECONDS=1800` (30 minutes) initially
   - Monitor a few jobs, check actual timings in logs
   - Reduce timeout if pages consistently finish in <300 seconds
   - Example workflow:
	 ```bash
	 OLLAMA_TIMEOUT_SECONDS=1800 python -m uvicorn app.main:app
	 # Extract 3-5 sample PDFs, watch logs
	 # If text_extraction typically 45-90s, reduce to:
	 OLLAMA_TIMEOUT_SECONDS=600   # 10 minutes buffer
	 ```

3. **Use per-request timeout override** (if implemented):
   ```bash
   # POST /vlm/... with ?timeout_seconds=1800
   # Allows tuning per P&ID complexity without restarting
   ```

4. **GPU troubleshooting on hybrid-graphics laptops**:
   - Ollama may auto-detect VRAM conservatively
   - Set `OLLAMA_NUM_GPU=999` to force max offload (already default)
   - If still CPU-only, manually specify a Modelfile:
	 ```dockerfile
	 FROM qwen3-vl:4b
	 PARAMETER num_gpu 999
	 ```
	 Then: `ollama create qwen3-vl-gpu -f Modelfile`

---

## Summary Table

| Setting | Default | Min | Max | Impact |
|---------|---------|-----|-----|--------|
| `OLLAMA_TIMEOUT_SECONDS` | 1200 | 300 | 3600 | How long to wait for VLM inference |
| `OLLAMA_NUM_CTX` | 12288 | 4096 | 32768 | Token window size (bigger = slower, but needed for dense PDFs) |
| `OLLAMA_NUM_GPU` | 999 | 0 | 999 | GPU layer offload (999 = as many as fit, 0 = CPU-only) |
| `OLLAMA_VLM_MODEL` | qwen3-vl:4b | — | — | Model choice (pick fastest for your VRAM) |

---

## Still Timing Out?

If you've tried the above and still hit timeouts:

1. **Check Ollama logs**:
   ```bash
   # If running via terminal:
   ollama serve  # Watch output for warnings/errors

   # If running via systemd:
   journalctl -u ollama -f
   ```

2. **Check API logs** (structured JSON):
   ```bash
   # Look for:
   extraction_stage_timing: stage=text_extraction, duration_seconds=1200+
   # If >1200s: Ollama is genuinely slow on your hardware

   # Check for errors:
   ollama_unreachable, ollama_timeout_retry
   ```

3. **Profile the model**:
   ```bash
   # Manually time a request:
   time curl -X POST http://localhost:11434/api/chat \
	 -H "Content-Type: application/json" \
	 -d '{
	   "model": "qwen3-vl:4b",
	   "messages": [{"role": "user", "content": "Process this image..."}],
	   "stream": false,
	   "options": {"num_ctx": 12288, "num_gpu": 999}
	 }'
   ```
   - Note the wall-clock time
   - If >1200s, try reducing `num_ctx` or switching to a smaller/faster model

4. **Consider alternative VLM models** (if qwen3-vl:4b is too slow):
   ```bash
   # Faster options:
   ollama pull qwen3-vl:1.8  # Smaller variant
   ollama pull llava:7b      # Faster, less accurate
   ollama pull llama2:7b     # Text-only (if you don't need vision)

   # Then update .env:
   OLLAMA_VLM_MODEL=qwen3-vl:1.8
   ```

---

## Questions?

- Refer to your structured logs (search for `extraction_stage_timing` in JSON)
- Check [Ollama docs](https://github.com/ollama/ollama) for environment variables
- Monitor [Ollama GitHub issues](https://github.com/ollama/ollama/issues) for GPU offload bugs on your hardware
