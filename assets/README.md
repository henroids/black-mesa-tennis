Images used by the project README. Original artwork only. No game assets, logos or
screenshots are stored in this repository.

- `logo.png`: the project logo (two dot-grid table-tennis paddles), the source for every icon.
- `icon.png`: the README icon, made from `logo.png`.

`tools/logo/make_logo.py` rebuilds every logo asset from `logo.png` (README icon, app icons,
the faint start-screen logo); the loading screen's copy is the dot map in
`stage2/native/tabletennis_app.h` (TtDrawLoadingLogo):

    python3 tools/logo/make_logo.py assets/logo.png . /tmp
