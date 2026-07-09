# Daalder — landing page

Static marketing site for Daalder (Dutch Telegram bot that tracks product
prices and pings you on a drop). Hand-written HTML/CSS/vanilla JS — no
framework, no bundler, no build step. Fully self-contained: nothing here
depends on the bot code in `../daalder/`.

The bot's Telegram handle is a fixed value repeated across `index.html`: the
CTA anchors (header, hero, slot-CTA, sticky mobile bar) and the `sameAs`
field of the JSON-LD block in `<head>`. If the handle ever changes, search
`index.html` for `t.me/DaalderPrijsBot` and update every occurrence.

## Run locally

Any static file server works, e.g.:

```sh
cd web
python3 -m http.server 8000
```

Then open `http://localhost:8000`.

## Deploy to Vercel

1. New Project → import this repo.
2. Set **Root Directory** to `web`.
3. Framework preset: **Other** (no build command, no output directory
   override needed — `vercel.json` handles headers/caching).
4. Deploy.

## Deploy to Railway

1. New Service → deploy from this repo.
2. Set the service's root directory to `web`.
3. Railway will detect the `Dockerfile` and build it (`caddy:alpine` serving
   the static files). Caddy binds to Railway's injected `$PORT` via the
   `Caddyfile` — no port is hardcoded.
4. Deploy.

## One or the other, not both

Host the landing page on **either** Vercel or Railway — pick one. The bot
itself (`../daalder/`) remains a separate, independent Railway **worker**
service (see the root `README.md` / `Procfile`); it has no relation to how
or where this static site is hosted.

## Before going live

- Replace `assets/og-image.png` with a real product/bot screenshot when
  available (current one is a branded placeholder).
- Update the placeholder domain (`https://daalder.app`) in `index.html`
  (canonical, Open Graph, Twitter, JSON-LD), `robots.txt`, and
  `sitemap.xml` once the production domain is known.
- Update the footer's `mailto:` and `Over` links once real destinations
  exist.
