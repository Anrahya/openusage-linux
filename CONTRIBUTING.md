# Contributing to OpenUsage for Linux

Thanks for helping! This project is a community Linux port of
[OpenUsage](https://github.com/robinebers/openusage) and there's a very
concrete way you can help even if you've never written a line of code here:
**verify a provider with your own subscription.**

## Provider verification status

| Provider | Status | What it needs |
|----------|--------|---------------|
| Codex    | ✅ Verified live | — |
| Claude   | 🧪 Ported from upstream, needs a real subscription | Pro/Max plan + Claude Code login |
| Cursor   | ✅ Verified live (Pro+ Auto usage is the headline meter) | — |
| OpenCode | ✅ Verified live (Go key in `opencode.db` + `session_message` logs) | — |
| Grok     | ✅ Weekly pool verified (OpenCode xAI or `grok login`); spend tiles need Grok CLI sessions | Official `grok login` for Today/Yesterday/30d |

### How to verify (5 minutes)

1. `git clone` this repo and run `./install.sh`.
2. Make sure you're logged in with the tool (`claude`, `grok login`, or OpenCode xAI).
3. Run `openusage-linux --json | python3 -m json.tool`.
4. Open the top-bar popover and the terminal output side by side.
5. Open an issue using the **Provider verification** template with:
   - your distro + GNOME version,
   - which rows render correctly / incorrectly,
   - the relevant JSON snippet — **redact your email and any tokens first**.

If something is wrong, include the error from
`journalctl --user -t gnome-shell -f` (for extension issues). Fixes are very
welcome — provider logic lives in `openusage_linux/core/providers/<name>/`
and every fix should come with a fixture test in `tests/`.

## Development setup

```bash
./install.sh                      # CLI + extension, no root
python3 -m unittest discover -s tests
```

The upstream macOS app (github.com/robinebers/openusage) is the reference for
all API contracts — port from its Swift sources rather than guessing.

## Making changes

- **Providers:** keep the three-part structure (`auth.py`, `client.py`,
  `mapper.py`), read existing credentials instead of adding login flows, and
  parse defensively (unknown fields → "No data", never a crash).
- **CLI/JSON:** the `--json` contract is consumed by the extension and
  Waybar; keep existing keys stable.
- **Extension:** after editing, bump `metadata.json` version and rebuild the
  zip (see AGENTS.md). Remember GNOME Shell caches extension modules — a
  logout/login is needed to see changes.
- **Tests:** offline and fixture-driven; no network calls in `tests/`.

## Pull requests

Keep PRs focused (one provider fix, one feature), describe what you verified,
and note whether you tested against a live subscription. Small, reviewable
changes with tests merge fastest.

## Code of conduct

Be kind, assume good intent, keep criticism technical. That's the whole CoC.
