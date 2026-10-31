# black-mesa-tennis installer for Windows 10/11.
#
# Builds TableTennis.apk from YOUR OWN retail Xbox 360 ISO of "Rockstar Games presents
# Table Tennis". Nothing from the game is downloaded or included: the ISO you pick is read on
# this computer, and the APK it produces is for your own device only (don't share it; it
# contains data from your copy of the game).
#
# What it does: checks WSL/Ubuntu, asks for your ISO, installs the build tools inside Ubuntu,
# gets the latest black-mesa-tennis, runs stage 1 (once) and stage 2, and opens the folder
# with the finished APK. Run it again any time to update; finished steps are skipped.

$ErrorActionPreference = 'Stop'
$Distro  = 'Ubuntu-24.04'
$RepoUrl = 'https://github.com/henroids/black-mesa-tennis'
$Pkgs    = 'git curl unzip zip python3 cmake ninja-build clang lld openjdk-17-jdk-headless pkg-config patch rsync'
$StateDir = Join-Path $env:LOCALAPPDATA 'black-mesa-tennis'
$IsoMemo  = Join-Path $StateDir 'iso-path.txt'

function Say($text, $color = 'Gray') { Write-Host $text -ForegroundColor $color }
function Step($n, $text) { Write-Host ""; Write-Host "[$n/6] $text" -ForegroundColor Cyan }
function Fail($text) {
  Write-Host ""; Write-Host "Stopped: $text" -ForegroundColor Red
  Write-Host "If you report this, attach ~/tt-android/tt-stage1-logs.zip or tt-stage2-logs.zip"
  Write-Host "from Ubuntu (they contain logs only, no game files)."
  exit 1
}
# Run a bash command inside Ubuntu; output streams to this window.
function InUbuntu([string]$command, [switch]$AsRoot) {
  $wslArgs = @('-d', $Distro)
  if ($AsRoot) { $wslArgs += @('-u', 'root') }
  $wslArgs += @('--', 'bash', '-lc', $command)
  & wsl.exe @wslArgs | Out-Host
  return $LASTEXITCODE
}
function BashQuote([string]$s) { "'" + $s.Replace("'", "'\''") + "'" }

Clear-Host
Say "black-mesa-tennis installer" 'White'
Say "Builds an Android APK of Rockstar Games presents Table Tennis from YOUR OWN game disc image."
Say "No game files are downloaded or included. Don't share the APK you build." 'Yellow'

# ---------------------------------------------------------------- 1. WSL + Ubuntu
Step 1 "Checking WSL and Ubuntu"
$haveWsl = $null -ne (Get-Command wsl.exe -ErrorAction SilentlyContinue)
$distros = @()
if ($haveWsl) {
  $distros = (& wsl.exe -l -q 2>$null) -replace "`0", '' | ForEach-Object { $_.Trim() } | Where-Object { $_ }
}
if (-not ($distros -contains $Distro)) {
  Say "Ubuntu 24.04 for WSL isn't installed yet." 'Yellow'
  $answer = Read-Host "Install it now? Windows will ask for administrator permission (Y/n)"
  if ($answer -match '^[nN]') { Fail "WSL with Ubuntu 24.04 is required." }
  Start-Process wsl.exe -ArgumentList "--install -d $Distro" -Verb RunAs -Wait
  Say ""
  Say "Next:" 'White'
  Say " 1. Restart Windows if it asks you to."
  Say " 2. Ubuntu opens by itself: pick a user name and password, then close it."
  Say " 3. Double-click Install-Table-Tennis.cmd again."
  exit 0
}
if ((InUbuntu 'true') -ne 0) { Fail "Ubuntu doesn't start. Open the Ubuntu app once to finish its setup, then run this again." }
Say "  Ubuntu 24.04 is ready."

# ---------------------------------------------------------------- 2. ISO
Step 2 "Choosing your game disc image"
$iso = $null
if (Test-Path $IsoMemo) {
  $last = (Get-Content $IsoMemo -Raw).Trim()
  if ($last -and (Test-Path $last)) {
    $answer = Read-Host "Use $last ? (Y/n)"
    if ($answer -notmatch '^[nN]') { $iso = $last }
  }
}
if (-not $iso) {
  Add-Type -AssemblyName System.Windows.Forms
  $dialog = New-Object System.Windows.Forms.OpenFileDialog
  $dialog.Title = 'Choose your Table Tennis ISO (USA or Europe retail, from your own disc)'
  $dialog.Filter = 'Disc images (*.iso)|*.iso|All files (*.*)|*.*'
  if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { Fail "No ISO chosen." }
  $iso = $dialog.FileName
}
New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
Set-Content -Path $IsoMemo -Value $iso
$isoLinux = (& wsl.exe -d $Distro -- wslpath -a "$iso").Trim()
if (-not $isoLinux) { Fail "Couldn't translate the ISO path for Ubuntu." }
Say "  $iso"
Say "  (Only read, never modified. It's checked before anything big is built.)"

# ---------------------------------------------------------------- 3. tools
Step 3 "Installing build tools inside Ubuntu (first time only)"
$missingCheck = "for p in $Pkgs; do dpkg -s `$p >/dev/null 2>&1 || exit 1; done"
if ((InUbuntu $missingCheck) -ne 0) {
  if ((InUbuntu "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $Pkgs" -AsRoot) -ne 0) {
    Fail "Installing the build tools failed."
  }
}
Say "  Build tools ready."

# ---------------------------------------------------------------- 4. source
Step 4 "Getting the latest black-mesa-tennis"
$get = "if [ -d ~/black-mesa-tennis/.git ]; then cd ~/black-mesa-tennis && git checkout -q main && git pull -q; " +
       "else git clone -q $RepoUrl ~/black-mesa-tennis; fi"
if ((InUbuntu $get) -ne 0) { Fail "Couldn't download black-mesa-tennis from GitHub." }
Say "  Up to date."

# ---------------------------------------------------------------- 5. stage 1
Step 5 "Stage 1: checking your ISO and recompiling the game (first time: 1-2 hours)"
if ((InUbuntu ("bash ~/black-mesa-tennis/stage1/tt_android_stage1.sh " + (BashQuote $isoLinux))) -ne 0) {
  Fail "Stage 1 didn't finish."
}

# ---------------------------------------------------------------- 6. stage 2
Step 6 "Stage 2: building the APK"
if ((InUbuntu "cd ~/black-mesa-tennis && bash stage2/tt_android_stage2.sh") -ne 0) { Fail "Stage 2 didn't finish." }

$apk = Join-Path ([Environment]::GetFolderPath('UserProfile')) 'Downloads\TableTennis.apk'
Write-Host ""
if (Test-Path $apk) {
  Say "Done: $apk" 'Green'
  Say "Copy it to your device, install it, then on the start screen use 'Game disc' to pick your ISO there."
  Say "Recommended settings are in README.md."
  Start-Process explorer.exe -ArgumentList "/select,`"$apk`""
} else {
  Say "Done. The APK is in Ubuntu at ~/tt-android/TableTennis.apk" 'Green'
}
