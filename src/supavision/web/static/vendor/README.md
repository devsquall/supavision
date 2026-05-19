# Vendored frontend assets

These files are third-party libraries served directly from `/static/vendor/`
so the dashboard does not depend on any CDN at runtime. The 0.4.5.dev0
checkpoint cut over from CDN loads to local copies; see `SECURITY.md` for
context.

Each entry below records:
- The exact source URL used to fetch the asset.
- The version pinned in `base.html`.
- The license (always permissive — both libraries are MIT).
- The SHA-256 checksum captured at download time. If you re-vendor a new
  version, re-download, re-hash, and update both the file and this list.

| File | Version | Source | License | SHA-256 |
|---|---|---|---|---|
| `htmx-2.0.4.min.js` | 2.0.4 | https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js | [BSD-2-Clause](https://github.com/bigskysoftware/htmx/blob/master/LICENSE) | `e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447` |
| `xterm-5.5.0.css` | 5.5.0 | https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.css | [MIT](https://github.com/xtermjs/xterm.js/blob/master/LICENSE) | `ba8e6985669488981ccf40c0cefe3aba80722cb6c92de7ad628b0bd717faf2b6` |
| `xterm-5.5.0.js` | 5.5.0 | https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.js | [MIT](https://github.com/xtermjs/xterm.js/blob/master/LICENSE) | `1f991ac3b4b283ebf96e60ae23a00a52765dd3a2e46fa6fdda9f1aab032f7495` |
| `xterm-addon-fit-0.10.0.js` | 0.10.0 | https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.10.0/lib/addon-fit.js | [MIT](https://github.com/xtermjs/xterm.js/blob/master/LICENSE) | `bdaefa370b1bfc42ee88d46fe6072400902a4d4b2d45cd93438dda9b23c97089` |

## Verifying after pull

```bash
cd src/supavision/web/static/vendor
sha256sum -c <<EOF
e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447  htmx-2.0.4.min.js
ba8e6985669488981ccf40c0cefe3aba80722cb6c92de7ad628b0bd717faf2b6  xterm-5.5.0.css
1f991ac3b4b283ebf96e60ae23a00a52765dd3a2e46fa6fdda9f1aab032f7495  xterm-5.5.0.js
bdaefa370b1bfc42ee88d46fe6072400902a4d4b2d45cd93438dda9b23c97089  xterm-addon-fit-0.10.0.js
EOF
```

## Fonts

We do not vendor any font files. The dashboard uses a CSS system-font stack
(see `--font-sans` and `--font-mono` in `web/static/style.css`) — the browser
picks an installed sans-serif and monospace font. No download, no privacy
leak, consistent with the OS.
