Images used by the project README. Original artwork only. No game assets, logos or
screenshots are stored in this repository.

- `logo.png`: the paddles artwork (two dot-grid table-tennis paddles), the source for the logo.
- `app_icon_logo.png`: the app icon's version of the logo (square dots on a tiled background), used only for the app icon.
- `icon_round.png`: **the project logo**: the paddles on a round black badge with a 3-row
  dotted border. Used for the README, and (faint) on the start and loading screens.

`tools/logo/make_logo.py` rebuilds every logo asset (the round logo for the README and the faint
start-screen copy from `logo.png`, app icons from `app_icon_logo.png`); the loading screen's copy is the dot map in
`stage2/native/tabletennis_app.h` (TtDrawLoadingLogo):

    python3 tools/logo/make_logo.py assets/logo.png . /tmp
