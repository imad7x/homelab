# landing

The public landing page for the bare domain (the tunnel routes it to port 3000), and a
LAN-only visitor dashboard.

| Container | Port | What |
|---|---|---|
| landing | 3000 | nginx (unprivileged) serving the page in `site/` |
| landing-visitors | 3011 (LAN only) | visitor dashboard; also the `visitors` command |

## Editing

- Page: `site/index.html`, `site/style.css`, `site/app.js`; app logos in `site/icons/`.
- After editing `style.css`, `app.js` or `theme.js`, bump their versions in `index.html`
  so browsers don't keep the old file:

  ```sh
  cd site && for f in style.css app.js theme.js; do h=$(md5sum $f | cut -c1-10); sed -i -E "s#/$f(\?v=[0-9a-f]+)?\"#/$f?v=$h\"#" index.html; done
  ```

- `docker restart landing` picks up an nginx change, `docker restart landing-visitors` a
  dashboard change.

## Restoring

```sh
mkdir -p logs blocklist visitors-state && chmod 2775 logs && chmod 700 visitors-state
ln -sfn "$PWD/visitors/visitors.py" ~/.local/bin/visitors
cp cloudflare.env.example cloudflare.env && chmod 600 cloudflare.env   # then add the token
docker compose up -d
```
