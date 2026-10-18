# Pohst Signal – modified source

Pohst Signal is built on **Postiz v2.24.0** (https://github.com/gitroomhq/postiz-app/releases/tag/v2.24.0),
licensed under the GNU Affero General Public License v3.0 (AGPL-3.0). See `LICENSE`.

This repository contains every modification Pohst makes to the upstream release.
To reproduce Pohst Signal, run the official Postiz v2.24.0 Docker image and apply the files below.

## Modifications

| File | Purpose |
|---|---|
| `patches/postiz_update.py` | Sets per-plan post limits; rebrands interface text (all languages) |
| `patches/signal_text_overrides.json` | Replacement billing/FAQ text per language |
| `patches/postiz_r2_delete_fix.py` | Removes deleted media from Cloudflare R2 storage (upstream issue #1217) |
| `patches/signal-up.sh` | Starts the stack and applies the patches |
| `nginx/pohst-brand.css` | Branding and hidden features (CSS injected by nginx) |
| `nginx/sub_filter-rules.txt` | nginx response rewrites: page titles, CSS injection, third-party analytics disabled |

Pohst logos and brand assets are trademarks of Pohst and are not included.
