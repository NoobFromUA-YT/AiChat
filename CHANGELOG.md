# Changelog

## 0.3.0 - 2026-11-22
- First public release. CI covers Python 3.10 to 3.13 on Linux and Python 3.12 on Windows; the `.deb` and the AppImage are built and started in CI.
- **Renamed to avoid clashes with other projects**: command `aichat-desktop`, Python package `aichat_desktop`, config folder `aichat-desktop` (the first start moves your files from the old `aichat` folder), env vars `AICHAT_DESKTOP_*`, desktop file / icon `aichat-desktop`.
- `.deb`: man page, AppStream metadata, md5sums, changelog; install / purge verified in a scratch root. AppImage recipe fixed and built.
- Icon assets renamed `icon*.png|svg`; the assets folder is a proper package (clean builds on new setuptools).
- Themes: dark, light, dark window with light chat, or follow the system (Settings → Colours).
- Windows-aware code paths (config in `%APPDATA%`, `claude.exe` lookup, UTF-8 pipes, no console flash, windowed launcher); not verified on Windows yet.
- Read-only mode uses `--permission-mode default` (the long-standing name) instead of `manual`.
- Pasted images live in a private cache folder and are deleted after 7 days.
- Author, project links, SPDX license metadata, SECURITY.md, issue / PR templates, Dependabot for Actions.
- `tests/run.py` runs the suite on any OS; CI gained a non-blocking Windows job.

## 0.2.0
- Package layout (`pip install .`, `aichat` command (since renamed `aichat-desktop`), `.deb` and AppImage recipes, CI).
- First-run setup (subscription or API key, starting credit), API key button, Settings.
- Project folder per chat; Read only / Edit files / Full auto permission modes.
- Live streaming of tool calls and text; saved chats with Chats dialog; Copy links; Markdown export.
- Bundled original icon; MIT license.

## 0.1
- Single-file chat window around `claude -p`: message queue, rendered replies, credit counter, Compact.
