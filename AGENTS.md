# AGENTS.md — notes for AI coding agents

OpenUsage for Linux tracks AI subscription quotas, rate limits, and token spend
in a GNOME top-bar extension, a terminal CLI, and an optional GTK4 window.
This file tells you how to work in this repo without breaking things.

## Install & run

```bash
./install.sh            # rootless: venv install + top-bar extension
python3 -m unittest discover -s tests    # offline, no network needed
openusage-linux         # terminal dashboard (needs a logged-in provider CLI)
openusage-linux --json  # Waybar/extension JSON contract
```

## Repo map

- `openusage_linux/core/providers/<id>/` — one package per provider, always
  three parts: `auth.py` (reads credentials the provider's own CLI left on
  disk), `client.py` (HTTP), `mapper.py` (payload → `MetricLine`). Claude and
  OpenCode also have a local `scanner.py` for session-log spend history.
- `openusage_linux/core/base.py` — the shared data model (`ProviderSnapshot`,
  `MetricLine`). Every UI renders from this; don't bypass it.
- `openusage_linux/cli/` — `main.py` iterates the registry, `formatters.py`
  owns terminal + JSON output.
- `openusage_linux/ui/` — optional GTK4/Libadwaita window (`--gui`).
- `gnome-extension/` — GJS top-bar extension. Ships as a **built zip**; after
  editing `extension.js`/`stylesheet.css` you MUST bump `metadata.json`
  version and rebuild:
  `zip openusage@anrahya.github.io.shell-extension.zip metadata.json extension.js stylesheet.css openusage.svg codex.svg claude.svg cursor.svg opencode.svg`
- `tests/` — unittest, fixture-driven, offline.

## The provider porting blueprint

Providers are ported from the upstream macOS app (github.com/robinebers/openusage —
not vendored in this public repo). Its Swift sources define the exact
endpoints, headers, payload shapes, and auth flows; mirror them, don't guess.
Never ask users to paste tokens — read credentials the provider's own tool
already wrote to disk.

## Gotchas that have already bitten us

- **GNOME Shell is not GTK.** `St.Widget`/`St.Button` have no
  `set_tooltip_text` and friends — those are GTK APIs and crash the shell
  extension at runtime. Use `accessible_name` property assignment.
- **Extensions are module-cached.** After reinstalling, the running shell
  keeps the old code until the user logs out/in (Wayland). Tests of
  extension changes need a session restart.
- **Meter geometry is fixed-width.** The popover is 320px; meter troughs are
  264px (`METER_WIDTH` in extension.js). Fill widths and pace-tick offsets
  are computed from that constant.
- **JSON contract:** `render_waybar_json` emits a `providers` array plus
  top-level Waybar fields from the most-constrained provider, and a `prefs`
  object (`period`, `metric`, `refresh_interval`, `show_total_spend`). Keep
  both stable — status bars and the extension depend on them.
- Rate-limit severity bands follow the macOS app: warning ≥ 80%, critical ≥ 90%.
- Codex auth writes must stay atomic with `0600` permissions.

## Verification reality

Codex and OpenCode are verified against live subscriptions. Claude and Cursor
are faithful ports awaiting verification by users who hold those
subscriptions — see CONTRIBUTING.md for the verification workflow. Keep
parsing defensive: unknown payloads should render "No data", never crash.
