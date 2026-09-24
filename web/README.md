# RepoLens web

The RepoLens website: the landing page, a live ingest progress view, and one
server-rendered, shareable page per indexed repo. It talks to the RepoLens API
(the FastAPI app in the repository root) through its own `/api/*` route
handlers.

```bash
npm install
cp .env.example .env.local   # REPOLENS_API_URL (server-only), SITE_URL
npm run dev
```

See the root [README](../README.md#website-web-phase-3) for how it works.
