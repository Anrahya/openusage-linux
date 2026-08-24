---
name: setup-openusage
description: >-
  Installs OpenUsage for Linux on the user's machine from this repo (GNOME
  top-bar extension, CLI, optional Waybar JSON). Use when the user asks to
  install OpenUsage, set up this repo, clone the GitHub URL and get it
  running, or enable the quota tracker / top-bar icon.
---

# Set up OpenUsage for the user

Follow the **Install this for the user** playbook in the repo-root `AGENTS.md`.
That file is the source of truth.

Short version:

1. Need Python 3.9+. Do not `sudo pip`.
2. From the repo root: `./install.sh` (or `./install.sh --skip-gui`).
3. Ensure `~/.local/bin` is on `PATH`.
4. Verify with `openusage-linux --list` then `openusage-linux`.
5. Never ask for API keys or tokens — providers are detected from local CLIs.
6. GNOME Shell 45–50 gets the top-bar icon (Wayland: logout/in after updates).
   Other desktops get the CLI; Hyprland/Sway can use `openusage-linux --json`
   in Waybar.
7. Stop when it works. Do not start coding unless they asked.
