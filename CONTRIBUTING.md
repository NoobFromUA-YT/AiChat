# Contributing

Bug reports and pull requests are welcome at https://github.com/NoobFromUA-YT/AiChat, especially from Windows users (see the README).

```bash
pip install PyQt6 pyflakes
python3 tests/run.py                 # must pass; offscreen, temp config folder, no real claude, no network
python3 -m pyflakes aichat_desktop tests tools
```

- Tests never touch your real config: set `config.CONFIG_DIR` to a temp folder (see `tests/test_features.py`).
- The window talks to Claude only through `aichat_desktop/worker.py`; new `claude` flags belong there and in `config.PERMISSION_MODES`.
- Keep `pyproject.toml`'s version equal to `aichat_desktop/__init__.py` (a test checks it) and add a line to `CHANGELOG.md`.
- Never put keys, chats or personal paths in tests, screenshots or fixtures (`tools/make_screenshots.py` uses a throw-away folder).
