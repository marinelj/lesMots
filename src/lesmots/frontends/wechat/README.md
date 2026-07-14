# LesMots · 温故 — 微信小程序前端

This folder is the WeChat Mini Program client. It is **not** part of the Python
package (excluded from the wheel in `pyproject.toml`) — open this directory as a
project in 微信开发者工具 and it talks to the same HTTP API as the web frontend
(`../web/`).

## Status

Phase 1 (this commit): project skeleton — app shell, login flow, API client.
Phase 2: full pages (story / bank / feed) — tracked in the M2 milestone.

## Setup

1. Get an AppID at mp.weixin.qq.com and put it in `project.config.json`.
2. Deploy the backend with `LESMOTS_WX_APPID` / `LESMOTS_WX_SECRET` set.
3. Point `utils/api.js` `BASE_URL` at your backend (CloudBase Run domain).

## Auth flow

`app.js` runs `wx.login()` on launch → posts the code to `/api/login` →
stores the returned session token → `utils/api.js` sends it as
`Authorization: Bearer <token>` on every request. No cookies involved.
