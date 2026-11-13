# Lexicon Render dashboard

`src/lesmots/lexrender/static/dashboard.html` is a standalone HTML page with inline CSS,
JavaScript, and SVG. Serve it on the same host as the `/api/dashboard/*` JSON endpoints.
There is no build step, external asset request, or runtime library.

## Preview with canned JSON

From the repository root:

```sh
python3 -m http.server 8767 --bind 127.0.0.1
```

Open <http://127.0.0.1:8767/src/lesmots/lexrender/static/dashboard.html?demo=1>.
The demo loads `/tests/fixtures/dashboard/*.json` from that server, including a separate
exposure history for each of its 12 entries. It does not read or send a saved sign-in
token. Its fixed 30-day period ends on November 14, 2026, so the sample remains useful
over time. The 168 exposures reconcile across entries, timeline, surfaces, and histories.

## Live data

Open the page without `?demo=1`. It reads `localStorage["lexrender_token"]`, or asks for
a sign-in token when none is saved. Every API request sends `Authorization: Bearer
<token>`. A 401 from any endpoint clears the saved token, cancels pending requests,
closes the history panel, and returns to sign-in. Sign out also clears the token.
If browser storage is unavailable, a pasted token works for the current tab.

The page uses these same-host endpoints:

| Endpoint | Purpose |
| --- | --- |
| `GET /api/dashboard/summary` | Totals, lexicon version, and section coverage |
| `GET /api/dashboard/entries?sort=exposures\|recent\|phrase` | Sorted entries; the API controls live ordering |
| `GET /api/dashboard/timeline?days=30` | Daily exposure counts |
| `GET /api/dashboard/surfaces` | Exposure counts by surface |
| `GET /api/dashboard/entry/<encoded id>` | An entry's exposure history |

The live chart shows 30 UTC calendar days ending today, filling absent dates with zero
recorded exposures. Point values are available on hover, keyboard focus, and in a
screen-reader table. Dates in the entries table use UTC; history timestamps use the
browser's local time. Zero entries produce 0% coverage.

Phrases open their supplied links in a new tab. Select the rest of a row, or its history
button, to open the side panel. The panel keeps keyboard focus inside it, closes with
Escape, and returns focus to the entry's history button. Theme follows the system's
`prefers-color-scheme`; the layout and chart adapt down to 375px.

## Verification

The Python suite checks fixture consistency. Browser regression tests run in their own
CI job using Node 22 and Playwright, both used only for testing. To run them locally:

```sh
npm install --prefix /tmp/lexrender-browser-tests playwright@1.62.1
/tmp/lexrender-browser-tests/node_modules/.bin/playwright install chromium
PLAYWRIGHT_MODULE=/tmp/lexrender-browser-tests/node_modules/playwright/index.mjs \
  node --test tests/dashboard.test.mjs
```

The test starts and stops its own static server. It covers demo and live requests,
sign-in and sign-out, all five endpoints returning 401, sorting and response races,
retry flows, empty and sparse data, unsafe HTML and links, keyboard interaction,
light and dark themes, and 375px layouts. Set `DASHBOARD_SCREENSHOTS=1` when running
the browser tests to refresh the images in `docs/screenshots/dashboard/`.
