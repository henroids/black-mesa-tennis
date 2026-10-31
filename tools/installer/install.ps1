# black-mesa-tennis installer for Windows 10/11.
#
# Builds TableTennis.apk from YOUR OWN retail Xbox 360 ISO of "Rockstar Games presents
# Table Tennis". Nothing from the game is downloaded or included: the ISO you pick is read on
# this computer, and the APK it produces is for your own device only (don't share it; it
# contains data from your copy of the game).
#
# Look: same theme as the in-app start screen (black, GamePocket pixel font, inverted white
# bar on the selected row). Keyboard: Up/Down to move, Enter to select, Left/Right to change
# the disc, Esc to quit. The mouse works too.
#
# Build steps (run in the background, shown in the window): build tools inside Ubuntu (as root,
# so no password prompts), clone/update black-mesa-tennis, stage 1 (skips finished steps),
# stage 2, then the APK is in your Downloads folder.

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$Distro   = 'Ubuntu-24.04'
$RepoUrl  = 'https://github.com/henroids/black-mesa-tennis'
$Pkgs     = 'git curl unzip zip python3 cmake ninja-build clang lld openjdk-17-jdk-headless pkg-config patch rsync'
$StateDir = Join-Path $env:LOCALAPPDATA 'black-mesa-tennis'
$IsoMemo  = Join-Path $StateDir 'iso-path.txt'
$ApkPath  = Join-Path ([Environment]::GetFolderPath('UserProfile')) 'Downloads\TableTennis.apk'

# ---------------------------------------------------------------- theme (matches the start screen)
$BLACK = [System.Drawing.Color]::Black
$WHITE = [System.Drawing.Color]::FromArgb(250, 250, 250)
$GREY  = [System.Drawing.Color]::FromArgb(154, 154, 154)
$DIM   = [System.Drawing.Color]::FromArgb(74, 74, 74)
$ICON  = [System.Drawing.Color]::FromArgb(122, 122, 122)

$fonts = New-Object System.Drawing.Text.PrivateFontCollection
$fontFile = Join-Path $PSScriptRoot '..\..\stage2\app\assets\fonts\GamePocket-Regular.ttf'
$family = $null
if (Test-Path $fontFile) {
  try { $fonts.AddFontFile((Resolve-Path $fontFile).Path); $family = $fonts.Families[0] } catch { }
}
function PixelFont([float]$size) {
  if ($family) { New-Object System.Drawing.Font($family, $size, [System.Drawing.GraphicsUnit]::Pixel) }
  else { New-Object System.Drawing.Font('Consolas', $size, [System.Drawing.GraphicsUnit]::Pixel) }
}

# ---------------------------------------------------------------- state
$script:iso = $null
if (Test-Path $IsoMemo) {
  $last = (Get-Content $IsoMemo -Raw).Trim()
  if ($last -and (Test-Path $last)) { $script:iso = $last }
}
$script:job = $null
$script:buildState = 'Ready'
$script:logLines = New-Object System.Collections.Generic.List[string]
$script:selected = 0
$script:quitArmed = $false

function HaveUbuntu {
  if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) { return $false }
  $list = (& wsl.exe -l -q 2>$null) -replace "`0", '' | ForEach-Object { $_.Trim() } | Where-Object { $_ }
  return ($list -contains $Distro)
}
$script:ubuntu = HaveUbuntu

# ---------------------------------------------------------------- window
$form = New-Object System.Windows.Forms.Form
$form.Text = 'Table Tennis'
$form.BackColor = $BLACK
$form.ClientSize = New-Object System.Drawing.Size(1000, 600)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedSingle'
$form.MaximizeBox = $false
$form.KeyPreview = $true

function NewLabel([float]$size, $color) {
  $l = New-Object System.Windows.Forms.Label
  $l.Font = PixelFont $size
  $l.ForeColor = $color
  $l.BackColor = [System.Drawing.Color]::Transparent
  $l.AutoSize = $true
  return $l
}

$clock = NewLabel 18 $GREY
$clock.Location = New-Object System.Drawing.Point(18, 10)
$form.Controls.Add($clock)
$corner = NewLabel 18 $GREY
$corner.Text = 'Your own disc only'
$corner.Location = New-Object System.Drawing.Point(800, 10)
$form.Controls.Add($corner)

# menu rows ------------------------------------------------------
$rowPanels = @(); $rowLabels = @(); $rowValues = @()
$rowsTop = 46; $rowHeight = 34; $rowsLeft = 250; $rowsWidth = 750
for ($i = 0; $i -lt 5; $i++) {
  $p = New-Object System.Windows.Forms.Panel
  $p.Location = New-Object System.Drawing.Point($rowsLeft, ($rowsTop + $i * $rowHeight))
  $p.Size = New-Object System.Drawing.Size($rowsWidth, $rowHeight)
  $p.BackColor = $BLACK
  $label = NewLabel 24 $WHITE
  $label.Location = New-Object System.Drawing.Point(12, 4)
  $value = NewLabel 24 $GREY
  $value.AutoSize = $false
  $value.TextAlign = 'MiddleRight'
  $value.Size = New-Object System.Drawing.Size(420, ($rowHeight - 4))
  $value.Location = New-Object System.Drawing.Point(($rowsWidth - 438), 2)
  $p.Controls.Add($label); $p.Controls.Add($value)
  $index = $i
  foreach ($c in @($p, $label, $value)) {
    $c.Add_Click({ param($s, $e) OnRowClick ([int]$s.Tag) }.GetNewClosure())
    $c.Tag = $index
  }
  $form.Controls.Add($p)
  $rowPanels += $p; $rowLabels += $label; $rowValues += $value
}

# progress log ---------------------------------------------------
$log = NewLabel 15 $DIM
$log.AutoSize = $false
$log.Location = New-Object System.Drawing.Point(($rowsLeft + 12), 230)
$log.Size = New-Object System.Drawing.Size(720, 300)
$form.Controls.Add($log)

$message = NewLabel 17 $GREY
$message.AutoSize = $false
$message.Location = New-Object System.Drawing.Point(18, 548)
$message.Size = New-Object System.Drawing.Size(600, 44)
$form.Controls.Add($message)

# help glyphs (bottom right), like the start screen's A / B hints -
function Glyph([string]$text, [int]$x, [int]$width) {
  $g = New-Object System.Windows.Forms.Label
  $g.Text = $text
  $g.Font = PixelFont 13
  $g.ForeColor = $BLACK
  $g.BackColor = $ICON
  $g.TextAlign = 'MiddleCenter'
  $g.Size = New-Object System.Drawing.Size($width, 22)
  $g.Location = New-Object System.Drawing.Point($x, 562)
  $path = New-Object System.Drawing.Drawing2D.GraphicsPath
  $path.AddArc(0, 0, 22, 22, 90, 180); $path.AddArc($width - 22, 0, 22, 22, 270, 180); $path.CloseFigure()
  $g.Region = New-Object System.Drawing.Region($path)
  $form.Controls.Add($g)
}
function HelpText([string]$text, [int]$x) {
  $h = NewLabel 17 $GREY
  $h.Text = $text
  $h.Location = New-Object System.Drawing.Point($x, 563)
  $form.Controls.Add($h)
}
Glyph 'Enter' 640 58; HelpText 'Select' 702
Glyph 'Esc' 790 44;  HelpText 'Back' 838
Glyph '<>' 902 30;   HelpText 'Change' 936

# ---------------------------------------------------------------- rows
function Rows {
  $rows = @()
  if (-not $script:ubuntu) { $rows += 'ubuntu' }
  $rows += 'disc', 'build', 'open', 'quit'
  return $rows
}
function RowText($kind) {
  switch ($kind) {
    'ubuntu' { return @('Install Ubuntu (WSL)', 'Needed once') }
    'disc'   { return @('Game disc', $(if ($script:iso) { Split-Path $script:iso -Leaf } else { 'Choose' })) }
    'build'  { return @('Build APK', $script:buildState) }
    'open'   { return @('Open APK folder', $(if (Test-Path $ApkPath) { 'TableTennis.apk' } else { '' })) }
    'quit'   { return @('Quit', '') }
  }
}
function Render {
  $rows = Rows
  if ($script:selected -ge $rows.Count) { $script:selected = $rows.Count - 1 }
  for ($i = 0; $i -lt $rowPanels.Count; $i++) {
    if ($i -ge $rows.Count) { $rowPanels[$i].Visible = $false; continue }
    $rowPanels[$i].Visible = $true
    $t = RowText $rows[$i]
    $on = ($i -eq $script:selected)
    $value = $t[1]
    if ($on -and $rows[$i] -eq 'disc') { $value = [char]0x2039 + ' ' + $value + ' ' + [char]0x203A }
    $rowLabels[$i].Text = $t[0]
    $rowValues[$i].Text = $value
    $rowPanels[$i].BackColor = $(if ($on) { $WHITE } else { $BLACK })
    $rowLabels[$i].ForeColor = $(if ($on) { $BLACK } else { $WHITE })
    $rowValues[$i].ForeColor = $(if ($on) { $BLACK } else { $GREY })
  }
  $clock.Text = (Get-Date).ToString('h:mm tt')
  $count = $script:logLines.Count
  $start = [Math]::Max(0, $count - 15)
  $log.Text = ($script:logLines.GetRange($start, $count - $start) -join "`n")
}
function Say([string]$text) { $message.Text = $text }

function PickIso {
  if ($script:job) { Say 'Wait for the build to finish.'; return }
  $dialog = New-Object System.Windows.Forms.OpenFileDialog
  $dialog.Title = 'Choose your Table Tennis ISO (USA or Europe retail, from your own disc)'
  $dialog.Filter = 'Disc images (*.iso)|*.iso|All files (*.*)|*.*'
  if ($dialog.ShowDialog($form) -eq [System.Windows.Forms.DialogResult]::OK) {
    $script:iso = $dialog.FileName
    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
    Set-Content -Path $IsoMemo -Value $script:iso
    Say 'Disc chosen. It is only read, never modified.'
  }
  Render
}

function InstallUbuntu {
  try {
    Start-Process wsl.exe -ArgumentList "--install -d $Distro" -Verb RunAs -Wait
    Say 'Restart Windows if asked, finish Ubuntu''s setup (user name + password), then open this again.'
  } catch {
    Say 'Ubuntu install was cancelled.'
  }
}

# ---------------------------------------------------------------- build (background job)
$BuildJob = {
  param($Distro, $RepoUrl, $Pkgs, $Iso)
  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
  function U([string]$command, [switch]$AsRoot) {
    $a = @('-d', $Distro); if ($AsRoot) { $a += @('-u', 'root') }; $a += @('--', 'bash', '-lc', $command)
    & wsl.exe @a 2>&1 | ForEach-Object { "$_" }
    "##EXIT $LASTEXITCODE"
  }
  function Q([string]$s) { "'" + $s.Replace("'", "'\''") + "'" }
  $isoLinux = (& wsl.exe -d $Distro -- wslpath -a "$Iso" | Out-String).Trim()
  "##STEP Build tools"
  # (no double quotes in commands passed to wsl.exe: Windows PowerShell 5.1 would mangle them)
  U ("need=`$(for p in $Pkgs; do dpkg -s `$p >/dev/null 2>&1 || echo `$p; done); " +
     "if [ -z `${need:+x} ]; then echo 'build tools ready'; " +
     "else apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq `$need || exit 1; fi") -AsRoot
  "##STEP Getting the latest version"
  U ("if [ -d ~/black-mesa-tennis/.git ]; then cd ~/black-mesa-tennis && git checkout -q main && git pull -q && echo 'up to date'; " +
     "else git clone -q $RepoUrl ~/black-mesa-tennis && echo 'downloaded'; fi")
  "##STEP Stage 1: checking your disc, recompiling (first time 1-2 h)"
  U ("bash ~/black-mesa-tennis/stage1/tt_android_stage1.sh " + (Q $isoLinux))
  "##STEP Stage 2: building the APK"
  U "cd ~/black-mesa-tennis && bash stage2/tt_android_stage2.sh"
  "##END"
}

function StartBuild {
  if ($script:job) { Say 'Already building.'; return }
  if (-not $script:ubuntu) { Say 'Install Ubuntu (WSL) first.'; return }
  if (-not $script:iso) { Say 'Choose your game disc first.'; PickIso; if (-not $script:iso) { return } }
  $script:logLines.Clear()
  $script:buildState = 'Building'
  $script:failed = $null
  $script:job = Start-Job -ScriptBlock $BuildJob -ArgumentList $Distro, $RepoUrl, $Pkgs, $script:iso
  Say 'Building. You can leave this window open; it updates by itself.'
  $timer.Start()
  Render
}

# The job prints "##STEP name" before each part and "##EXIT n" after each Ubuntu command;
# any non-zero exit stops the build at that step.
function HandleLine([string]$line) {
  if ($line -like '##STEP *') { $script:step = $line.Substring(7); $script:buildState = $script:step; Say $script:step; return }
  if ($line -like '##EXIT *') {
    if ([int]$line.Substring(7) -ne 0 -and -not $script:failed) { $script:failed = $script:step }
    return
  }
  if ($line -eq '##END') { return }
  $script:logLines.Add($line)
}

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 300
$timer.Add_Tick({
  if ($script:job) {
    foreach ($line in (Receive-Job $script:job)) { HandleLine "$line" }
    if ($script:failed) {
      Stop-Job $script:job -ErrorAction SilentlyContinue
    }
    if ($script:job.State -ne 'Running') {
      foreach ($line in (Receive-Job $script:job)) { HandleLine "$line" }
      Remove-Job $script:job -Force -ErrorAction SilentlyContinue
      $script:job = $null
      if ($script:failed) {
        $script:buildState = 'Stopped'
        Say ("Stopped at: " + $script:failed + ". Logs: ~/tt-android/tt-stage1-logs.zip or tt-stage2-logs.zip (no game files).")
      } elseif (Test-Path $ApkPath) {
        $script:buildState = 'Done'
        Say 'Done. TableTennis.apk is in Downloads. Install it, then pick your ISO under Game disc on the device.'
      } else {
        $script:buildState = 'Done'
        Say 'Done. The APK is in Ubuntu at ~/tt-android/TableTennis.apk'
      }
    }
  }
  Render
})

# ---------------------------------------------------------------- input
function Activate([string]$kind) {
  switch ($kind) {
    'ubuntu' { InstallUbuntu }
    'disc'   { PickIso }
    'build'  { StartBuild }
    'open'   {
      if (Test-Path $ApkPath) { Start-Process explorer.exe -ArgumentList "/select,`"$ApkPath`"" }
      else { Say 'No APK yet. Build it first.' }
    }
    'quit'   { TryQuit }
  }
  Render
}
function TryQuit {
  if ($script:job -and -not $script:quitArmed) {
    $script:quitArmed = $true
    Say 'Still building. Press Esc again (or Quit) to stop the build and close.'
    return
  }
  if ($script:job) { Stop-Job $script:job -ErrorAction SilentlyContinue; Remove-Job $script:job -Force -ErrorAction SilentlyContinue }
  $form.Close()
}
function OnRowClick([int]$i) {
  $rows = Rows
  if ($i -ge $rows.Count) { return }
  if ($script:selected -eq $i) { Activate $rows[$i] } else { $script:selected = $i; Render }
}
$form.Add_KeyDown({
  param($s, $e)
  $rows = Rows
  switch ($e.KeyCode) {
    'Up'     { $script:selected = ($script:selected - 1 + $rows.Count) % $rows.Count; $script:quitArmed = $false; Render }
    'Down'   { $script:selected = ($script:selected + 1) % $rows.Count; $script:quitArmed = $false; Render }
    'Left'   { if ($rows[$script:selected] -eq 'disc') { PickIso } }
    'Right'  { if ($rows[$script:selected] -eq 'disc') { PickIso } }
    'Return' { Activate $rows[$script:selected] }
    'Escape' { TryQuit }
  }
  $e.Handled = $true
})
$form.Add_FormClosing({ if ($script:job) { Stop-Job $script:job -ErrorAction SilentlyContinue } })

$script:selected = $(if ($script:ubuntu) { if ($script:iso) { 1 } else { 0 } } else { 0 })
Say $(if (-not $script:ubuntu) { 'Ubuntu (WSL) is needed to build. Select "Install Ubuntu (WSL)".' }
      elseif (-not $script:iso) { 'Choose your own Table Tennis ISO (USA or Europe retail). No game files are included.' }
      else { 'Ready. Select "Build APK". Don''t share the APK: it contains data from your copy of the game.' })
Render
$clockTimer = New-Object System.Windows.Forms.Timer
$clockTimer.Interval = 15000
$clockTimer.Add_Tick({ Render })
$clockTimer.Start()
[void]$form.ShowDialog()
