# Frontend customisation — PlanB Network branding

Applied 2026-05-01 to the mempool deployment on `flagrant-feedback`.

The stock `mempool/frontend:latest` image is replaced with a custom image
built from the cloned source (`~/mempool-space`, tag v3.3.1).

Find the detailed session log [here](web-customisation.log).

## What was changed

| File (under `~/mempool-space/frontend/src/`) | Change |
|---|---|
| `app/components/svg-images/svg-images.component.html` | `mempoolSpace` SVG: "mempool" paths shifted to upper row; `<text>planb</text>` added below in orange (`#FF6B35`) |
| `app/shared/components/global-footer/global-footer.component.html` | 3× "Be your own explorer" → "PlanB Network signet playground" |
| `app/components/about/about.component.html` | 1× "Be your own explorer" → "PlanB Network signet playground" |
| `index.mempool.html` | `<title>` → "mempool Planb signet" |

The SVG change covers all three logo locations at once: desktop navbar,
mobile navbar, and footer branding block.

## How to rebuild after source edits

Auxiliary files must be staged into the `frontend/` build context before
building (they live elsewhere in the repo):

```bash
cp ~/mempool-space/docker/frontend/entrypoint.sh ~/mempool-space/frontend/
cp ~/mempool-space/docker/frontend/wait-for ~/mempool-space/frontend/
cp ~/mempool-space/nginx.conf ~/mempool-space/frontend/
cp ~/mempool-space/nginx-mempool.conf ~/mempool-space/frontend/

cd ~/mempool-space
docker build -t planb-mempool-frontend:latest \
  -f docker/frontend/Dockerfile frontend/
```

Then redeploy:

```bash
docker compose -f ~/mempool-space/docker-compose.yml up -d --no-deps web
```

## Repo-level fixes applied (required for the custom build to work)

Four bugs in the v3.3.1 repo's Docker/nginx setup were patched:

1. **`nginx.conf`** — listen directive was hardcoded to `127.0.0.1:80`.
   Changed to `__MEMPOOL_FRONTEND_HTTP_PORT__` so `entrypoint.sh` substitutes
   it with the `FRONTEND_HTTP_PORT` env var (`8080`), enabling the Docker port
   mapping `0.0.0.0:8080:8080`.

2. **`nginx-mempool.conf`** — proxy targets were hardcoded to `127.0.0.1:8999`
   (localhost inside the web container). Replaced with
   `__MEMPOOL_BACKEND_MAINNET_HTTP_HOST__:__MEMPOOL_BACKEND_MAINNET_HTTP_PORT__`
   so `entrypoint.sh` substitutes the `api` container hostname at runtime.

3. **`docker/frontend/Dockerfile`** — `nginx-mempool.conf` copy destination
   corrected to `/etc/nginx/conf.d/` (matching the nginx.conf include path and
   the chown that makes the directory writable by user 1000).

4. **`docker/frontend/entrypoint.sh`** — sed paths aligned to
   `/etc/nginx/conf.d/nginx-mempool.conf` to match the above.
