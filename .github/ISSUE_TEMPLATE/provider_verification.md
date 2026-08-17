---
name: Provider verification
about: Report results from testing a provider with your real subscription
title: "verify: <provider> on <distro>"
labels: verification
---

<!-- Thanks for testing! Redact your email address and any tokens before pasting anything. -->

**Provider:** Claude / Cursor / OpenCode
**Distro + version:**
**GNOME Shell version:** (`gnome-shell --version`)
**Plan type:** (e.g. Claude Max 20x, Cursor Pro, OpenCode Go)

## What renders correctly

- [ ] Top-bar popover opens with the provider section
- [ ] Quota/limit meters show sensible percentages
- [ ] Reset countdowns look right
- [ ] Spend history rows (Today / Yesterday / 30 days)
- [ ] Terminal output (`openusage-linux`) matches the popover

## What's wrong

<!-- Describe wrong numbers, missing rows, or errors. For extension problems,
     include the output of: journalctl --user -t gnome-shell -f -->

## JSON output (redacted!)

```json
<!-- openusage-linux --json | python3 -m json.tool — remove email/tokens -->
```

## Screenshots

<!-- Optional but very helpful: popover + mac app or provider dashboard for comparison -->
