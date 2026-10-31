Stage 2 - build the APK (see ../README.md and ../docs/BUILDING.md)
==================================================================
In the Ubuntu (WSL) window, after stage 1:

    cd ~/black-mesa-tennis && git pull && bash stage2/tt_android_stage2.sh

TableTennis.apk is copied to your Windows Downloads folder (or stays at
~/tt-android/TableTennis.apk). Don't share it: it contains data from your copy of the game.
If it fails, attach ~/tt-android/tt-stage2-logs.zip to an issue.
