#Requires -Version 5.0
<#
.SYNOPSIS
  Uninstall vklauncher on Windows

.EXAMPLE
  irm https://raw.githubusercontent.com/vk1111111/vklauncher/main/uninstall.ps1 | iex
#>

$ErrorActionPreference = "Stop"

$Prefix = if ($env:VKLAUNCHER_PREFIX) { $env:VKLAUNCHER_PREFIX } else { Join-Path $env:LOCALAPPDATA "vklauncher" }
$BinDir = Join-Path $Prefix "bin"

Write-Host "==> Removing Start Menu shortcut"
$ShortcutPath = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\vklauncher.lnk"
if (Test-Path $ShortcutPath) {
    Remove-Item $ShortcutPath -Force
}

Write-Host "==> Removing install directory: $Prefix"
if (Test-Path $Prefix) {
    Remove-Item -Recurse -Force $Prefix
}

# Remove from user PATH
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($UserPath) {
    $parts = $UserPath -split ";" | Where-Object { $_ -and ($_.TrimEnd("\") -ne $BinDir.TrimEnd("\")) }
    [Environment]::SetEnvironmentVariable("Path", ($parts -join ";"), "User")
    Write-Host "==> Removed from user PATH (if present)"
}

Write-Host "==> vklauncher uninstalled"
