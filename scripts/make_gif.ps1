<#
.SYNOPSIS
  Turn a screen recording (MP4) into a small, good-looking GIF with ffmpeg.
.EXAMPLE
  .\scripts\make_gif.ps1 -In recordings\demo.mp4 -Out docs\demo.gif -Start 8 -Duration 25
#>
param(
  [Parameter(Mandatory = $true)][string]$In,
  [string]$Out = "docs\demo.gif",
  [int]$Fps = 12,
  [int]$Width = 960,
  [double]$Start = 0,
  [double]$Duration = 0
)
$ErrorActionPreference = "Stop"

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  throw "ffmpeg was not found. Install it from https://ffmpeg.org/download.html, then reopen PowerShell."
}
if (-not (Test-Path $In)) { throw "Input file not found: $In" }

$palette = Join-Path $env:TEMP "demo_palette.png"
$trim = @()
if ($Start -gt 0)    { $trim += @("-ss", "$Start") }
if ($Duration -gt 0) { $trim += @("-t", "$Duration") }
$filters = "fps=$Fps,scale=${Width}:-1:flags=lanczos"

# Pass 1: build a colour palette for this clip. Pass 2: use it. This avoids the washed-out look of default GIFs.
& ffmpeg -y @trim -i $In -vf "$filters,palettegen=stats_mode=diff" $palette
if ($LASTEXITCODE -ne 0) { throw "ffmpeg failed while building the palette" }
& ffmpeg -y @trim -i $In -i $palette -lavfi "$filters [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=5" $Out
if ($LASTEXITCODE -ne 0) { throw "ffmpeg failed while writing the GIF" }

$mb = [math]::Round((Get-Item $Out).Length / 1MB, 1)
Write-Host "Wrote $Out ($mb MB)"
if ($mb -gt 10) {
  Write-Warning "That is large for a README. Try a shorter clip (-Duration), -Fps 10, or -Width 800."
}
