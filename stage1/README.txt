Table Tennis -> AYN Thor : Stage 1 build kit
=============================================

ONE-TIME SETUP (Windows 11)
1. Open PowerShell as Administrator and run:
       wsl --install -d Ubuntu-24.04
2. Reboot. Ubuntu opens by itself. Pick a username and password (remember the password).

RUN THE KIT (in the Ubuntu window)
3. Copy the kit into Ubuntu (replace YOURNAME with your Windows user name):
       sudo apt update && sudo apt install -y unzip
       cp /mnt/c/Users/YOURNAME/Downloads/tt-android-kit.zip ~ && cd ~ && unzip -o tt-android-kit.zip
4. Start the build, pointing it at your ISO. C:\ becomes /mnt/c/ and backslashes become /.
   Keep the quotes:
       bash ~/tt-android-kit/tt_android_stage1.sh "/mnt/c/Games/TableTennis.iso"
   It asks for your Ubuntu password once. Leave the window open; it takes 1-2 hours.
   It checks your ISO early (step 7). If the ISO is the wrong version it stops there.

SEND BACK
5. Whether it finishes or stops with an error, copy the logs to Windows:
       cp ~/tt-android/tt-stage1-logs.zip /mnt/c/Users/YOURNAME/Downloads/
   Then upload that zip to Claude. It has logs only, no game files.

Re-running the script is safe; finished steps are skipped.
