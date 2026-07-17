#Requires -Version 5.0
<#
.SYNOPSIS
  Install vklauncher on Windows

.EXAMPLE
  irm https://raw.githubusercontent.com/vk1111111/vklauncher/main/install.ps1 | iex

.EXAMPLE
  $env:VKLAUNCHER_VERSION = 'v0.1.0'
  irm https://raw.githubusercontent.com/vk1111111/vklauncher/main/install.ps1 | iex
#>

$ErrorActionPreference = "Stop"

$Repo = if ($env:VKLAUNCHER_REPO) { $env:VKLAUNCHER_REPO } else { "vk1111111/vklauncher" }
$Prefix = if ($env:VKLAUNCHER_PREFIX) { $env:VKLAUNCHER_PREFIX } else { Join-Path $env:LOCALAPPDATA "vklauncher" }
$Version = if ($env:VKLAUNCHER_VERSION) { $env:VKLAUNCHER_VERSION } else { "latest" }
$Asset = "vklauncher-windows-x86_64.zip"

function Write-Step([string]$Message) {
    Write-Host "==> $Message"
}

if ($Version -eq "latest") {
    $DownloadUrl = "https://github.com/$Repo/releases/latest/download/$Asset"
} else {
    $DownloadUrl = "https://github.com/$Repo/releases/download/$Version/$Asset"
}

$WorkDir = Join-Path ([System.IO.Path]::GetTempPath()) ("vklauncher-install-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $WorkDir | Out-Null

try {
    Write-Step "Installing vklauncher (windows-x86_64)"
    Write-Step "Download: $DownloadUrl"

    $ZipPath = Join-Path $WorkDir $Asset
    Invoke-WebRequest -Uri $DownloadUrl -OutFile $ZipPath -UseBasicParsing

    $ExtractDir = Join-Path $WorkDir "extract"
    Expand-Archive -Path $ZipPath -DestinationPath $ExtractDir -Force

    $Binary = Get-ChildItem -Path $ExtractDir -Recurse -Filter "vklauncher.exe" | Select-Object -First 1
    if (-not $Binary) {
        throw "Archive did not contain vklauncher.exe"
    }

    $BinDir = Join-Path $Prefix "bin"
    New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
    Copy-Item -Path $Binary.FullName -Destination (Join-Path $BinDir "vklauncher.exe") -Force

    $IconSrc = Get-ChildItem -Path $ExtractDir -Recurse -Include "vklauncher.ico", "icon_universal.png" |
        Select-Object -First 1
    $IconDestIco = Join-Path $Prefix "vklauncher.ico"
    $IconDestPng = Join-Path $Prefix "icon.png"

    if ($IconSrc) {
        if ($IconSrc.Extension -ieq ".ico") {
            Copy-Item $IconSrc.FullName $IconDestIco -Force
        } else {
            Copy-Item $IconSrc.FullName $IconDestPng -Force
            $PackagedIco = Get-ChildItem -Path $ExtractDir -Recurse -Filter "vklauncher.ico" | Select-Object -First 1
            if ($PackagedIco) {
                Copy-Item $PackagedIco.FullName $IconDestIco -Force
            }
        }
    } else {
        try {
            $RawPng = "https://raw.githubusercontent.com/$Repo/main/assets/icon_universal.png"
            Invoke-WebRequest -Uri $RawPng -OutFile $IconDestPng -UseBasicParsing
        } catch {
            Write-Step "Warning: could not download icon"
        }
    }

    $StartMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
    New-Item -ItemType Directory -Path $StartMenu -Force | Out-Null
    $ShortcutPath = Join-Path $StartMenu "vklauncher.lnk"
    $Wsh = New-Object -ComObject WScript.Shell
    $Shortcut = $Wsh.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = Join-Path $BinDir "vklauncher.exe"
    $Shortcut.WorkingDirectory = $BinDir
    $Shortcut.Description = "vklauncher - Terminal Minecraft launcher"
    if (Test-Path $IconDestIco) {
        $Shortcut.IconLocation = "$IconDestIco,0"
    } elseif (Test-Path $IconDestPng) {
        $Shortcut.IconLocation = "$IconDestPng,0"
    }
    $Shortcut.Save()
    Write-Step "Start Menu shortcut: $ShortcutPath"

    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not $UserPath) { $UserPath = "" }
    $parts = $UserPath -split ";" | Where-Object { $_ -and $_.Trim() -ne "" }
    if ($parts -notcontains $BinDir) {
        $NewPath = ($parts + $BinDir) -join ";"
        [Environment]::SetEnvironmentVariable("Path", $NewPath, "User")
        $env:Path = "$BinDir;$env:Path"
        Write-Step "Added to user PATH: $BinDir"
        Write-Step "Open a new terminal for PATH changes to apply."
    }

    Set-Content -Path (Join-Path $Prefix "install_prefix.txt") -Value $Prefix
    Write-Step "Installed: $(Join-Path $BinDir 'vklauncher.exe')"
    Write-Step "Done. Run: vklauncher"
}
finally {
    Remove-Item -Recurse -Force $WorkDir -ErrorAction SilentlyContinue
}
