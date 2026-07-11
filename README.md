# LesMots

Learn vocabulary **inside content you actually want to read.**

LesMots keeps a long-term memory of the words, phrases, and sentences you're learning. Every day it fetches popular internet content matching your interests (default: LLM, AI, state-of-the-art tech) and rewrites it in simple English — deliberately reusing the words you most need to see, picked by SM-2 spaced repetition. Reading the daily piece *is* the review.

## How it works

1. **Input** — add a word, phrase, or sentence; LesMots explains it in simple English plus your language (default: Chinese).
2. **Memory** — every entry is tracked in a JSON word bank: added time, exposure count, familiarity level (0–5).
3. **Daily job** — fetches a popular story for your interests and rewrites it (max 120 words) using bank words picked by the SM-2 rule (most-overdue first).
4. **Show me** — click the button (HTML) or run `lesmots show-me` (chat); the prepared content is displayed and every bank word it used gets its exposure count and familiarity updated.

## Install

```bash
git clone https://github.com/marine/lesmots && cd lesmots
pip install -e ".[dev]"
```

Requires Python 3.9+. Zero runtime dependencies (stdlib only).

### Choose an LLM provider

LesMots needs an LLM for explanations and rewriting. Either backend works:

```bash
# Option A — Anthropic
export ANTHROPIC_API_KEY=sk-ant-...

# Option B — any OpenAI-compatible provider (DeepSeek, Qwen, GLM, Moonshot, OpenAI, Ollama...)
export LESMOTS_API_KEY=sk-...
export LESMOTS_API_BASE=https://api.deepseek.com
export LESMOTS_MODEL=deepseek-chat
```

More option-B examples: Qwen/DashScope (`https://dashscope.aliyuncs.com/compatible-mode/v1`, `qwen-plus`), local Ollama (`http://localhost:11434/v1`, any local model, key can be anything). Without any key, LesMots still runs with a simple non-LLM fallback rewriter.

## HTML version

```bash
lesmots serve            # open http://127.0.0.1:8321
```

The UI has: an **add & explain** input, a **Show me** button, a **Word bank** table (word/phrase/sentence, added time, exposure count, familiarity), a **demonstration area** for the rewritten content, and an **original content area** showing the unmodified source with a link.

## Chat version

```bash
lesmots add "state of the art"        # add + explain (default language: Chinese)
lesmots add "inference" --lang French
lesmots show-me                       # show today's content, update exposures
lesmots words                         # word bank table
lesmots config --interests "robotics,biotech" --lang Japanese
```

## The daily job

```bash
lesmots daily
```

Cron example (every morning at 7):

```
0 7 * * * lesmots daily
```

If no prepared content exists when you hit **Show me**, LesMots generates it on the spot.

## Configuration

Stored in `~/.lesmots/lesmots.json` (override directory with `LESMOTS_HOME`):

| Key | Default |
|---|---|
| `language` | `Chinese` |
| `interests` | `LLM, AI, state-of-the-art tech` |
| `max_words` | `120` |
| `pick_limit` | `10` |

Env vars: `ANTHROPIC_API_KEY` **or** `LESMOTS_API_KEY` + `LESMOTS_API_BASE`; `LESMOTS_MODEL` (default `claude-haiku-4-5` on Anthropic, required otherwise); `LESMOTS_HOME`.

## Deploying to Cloud Run

The included `Dockerfile` runs `lesmots serve`, which binds `0.0.0.0` and honors Cloud Run's `PORT` env var (default 8321):

```bash
gcloud run deploy lesmots --source . --region REGION --allow-unauthenticated \
  --set-env-vars ANTHROPIC_API_KEY=sk-ant-...
```

**Persistence:** Cloud Run's filesystem is ephemeral — without extra setup, the word bank (`$LESMOTS_HOME/lesmots.json`) is lost whenever an instance is replaced. The image sets `LESMOTS_HOME=/data`; mount a GCS bucket there to persist it:

```bash
gcloud storage buckets create gs://YOUR_BUCKET --location REGION
gcloud run services update lesmots --region REGION \
  --add-volume name=data,type=cloud-storage,bucket=YOUR_BUCKET \
  --add-volume-mount volume=data,mount-path=/data
```

GCS FUSE mounts don't support concurrent writers, so also cap scaling with `--max-instances 1`. If you skip the mount, the app still works — the word bank just resets on each new instance.

## Word picking & familiarity

Scheduling is simplified SM-2 (see `src/lesmots/srs.py`). Each exposure counts as a "good" review: the word's interval grows by its ease factor, so well-known words appear less often. Familiarity is derived from the interval: 0 = new, 1 ≥ 1 day, 2 ≥ 3, 3 ≥ 7, 4 ≥ 21, 5 ≥ 60.

## Development

```bash
pytest          # offline — network and LLM are mocked
ruff check .
```

Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
