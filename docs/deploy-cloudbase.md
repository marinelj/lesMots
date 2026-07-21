# Deploying to 微信云托管 (WeChat CloudBase Run)

The mini-program frontend (`src/lesmots/frontends/wechat/`) needs a backend
reachable from mainland China. Google Cloud Run is not (GFW + no ICP filing),
so the China deployment runs on 微信云托管 — Tencent's container platform
bound to the mini-program account (AppID `wxcf87d1a5c350e39d`).

Two files in the repo root drive it:

- `Dockerfile.cloudbase` — same image as `Dockerfile`, but pip pulls from the
  Tencent mirror (the build farm is in the mainland).
- `container.config.json` — service config: port 8321, scale 0–1, env vars.

## Why max 1 instance

The word bank is a JSON file under `$LESMOTS_HOME`; there is no concurrent-
writer support (same constraint as the Cloud Run + GCS FUSE deployment).

## What replaces the Google pieces

| Cloud Run setup | CloudBase setup |
|---|---|
| Gemini (OpenAI-compatible endpoint) | Any mainland OpenAI-compatible LLM. DeepSeek: `LESMOTS_API_BASE=https://api.deepseek.com`, `LESMOTS_MODEL=deepseek-chat`. Qwen: `LESMOTS_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1`, `LESMOTS_MODEL=qwen-plus`. No code change — `llm.py` only needs the env vars. |
| GCS FUSE bucket mounted at `/data` | `cloudsync.py`: every save is mirrored into the environment's object storage via the tcb HTTP API, and pulled back into `/data` on server start. Enabled by `LESMOTS_TCB_ENV=<env id>` (needs `LESMOTS_WX_APPID`/`SECRET` for the access token). No volume mount exists in 云托管 service config, and `/data` is wiped on every scale-to-zero — do not disable this. |
| Secret Manager | 云托管 env vars (console or `container.config.json` `envParams` — do NOT commit secrets there; set `LESMOTS_API_KEY` and `LESMOTS_WX_SECRET` in the console). |

## One-time setup (account owner, in a browser)

1. Open <https://cloud.weixin.qq.com>, log in with the mini-program admin
   WeChat account, and 开通云托管 for AppID `wxcf87d1a5c350e39d`.
   Requires real-name/entity verification; billing is pay-as-you-go.
2. Create a service (e.g. `lesmots`).
3. In the console, set the secret env vars on the service:
   `LESMOTS_API_KEY` (LLM key), `LESMOTS_WX_SECRET` (mini-program AppSecret).
4. For CLI deploys: console → 设置 → CLI 密钥, generate a key pair.

## Deploying

Node + the CLI are installed outside PATH (this machine has no brew):

```sh
export PATH="$HOME/node-v24/bin:$PATH"
wxcloud login --appid wxcf87d1a5c350e39d --privateKey <CLI key from console>
wxcloud run:deploy . --serviceName lesmots
```

The build happens in the cloud from `Dockerfile.cloudbase`; no local Docker
needed. Alternatively, deploy from 微信开发者工具 (云托管 panel → 发布) or
upload a zip in the web console.

## Frontend wiring

Calling the backend with `wx.cloud.callContainer` (env ID + service name)
skips the request-domain whitelist and ICP filing entirely. Plain
`wx.request` to the service's public domain also works but needs the domain
whitelisted in the mini-program console. `utils/api.js` `BASE_URL` should
point at whichever is chosen.
