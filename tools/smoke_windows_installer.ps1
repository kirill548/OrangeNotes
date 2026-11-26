param(
    [Parameter(Mandatory)] [string] $Setup,
    [string] $Python = 'python'
)

$ErrorActionPreference = 'Stop'
$setupPath = (Resolve-Path -LiteralPath $Setup).Path
$scratch = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { $env:TEMP }
$install = Join-Path $scratch ('Orange Notes setup check ' + [Guid]::NewGuid().ToString('N'))
$executable = Join-Path $install 'OrangeNotes.exe'
$silent = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/NOCLOSEAPPLICATIONS')
$arguments = $silent + @(('/DIR="' + $install + '"'), '/TASKS=desktopicon')
$startMenu = Join-Path ([Environment]::GetFolderPath('Programs')) 'Orange Notes.lnk'
$desktop = Join-Path ([Environment]::GetFolderPath('DesktopDirectory')) 'Orange Notes.lnk'
$uninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\OrangeNotes.Desktop.Windows_is1'

function Run-Package([string] $File, [string[]] $Arguments) {
    $process = Start-Process -FilePath $File -ArgumentList $Arguments -Wait -PassThru -WindowStyle Hidden
    if ($process.ExitCode -ne 0) {
        throw "$File returned exit code $($process.ExitCode)"
    }
}

foreach ($shortcut in @($startMenu, $desktop)) {
    if (Test-Path -LiteralPath $shortcut) { throw "Installer smoke requires a clean user: $shortcut exists" }
}
if (Test-Path -LiteralPath $uninstallKey) { throw 'Installer smoke requires a clean Orange Notes installation entry.' }

Run-Package $setupPath $arguments
$registered = Get-ItemProperty -LiteralPath $uninstallKey
if ($registered.InstallLocation.TrimEnd('\') -ine $install) {
    throw 'The per-user uninstall entry does not match the custom location.'
}
foreach ($required in @('OrangeNotes.exe', '_internal', 'ai', 'stt')) {
    if (-not (Test-Path -LiteralPath (Join-Path $install $required))) { throw "Missing installed path: $required" }
}

$shell = New-Object -ComObject WScript.Shell
foreach ($shortcut in @($startMenu, $desktop)) {
    if (-not (Test-Path -LiteralPath $shortcut) -or $shell.CreateShortcut($shortcut).TargetPath -ine $executable) {
        throw "Shortcut is missing or does not point to the custom location: $shortcut"
    }
}

$markers = @('ai/setup-smoke-model.txt', 'stt/setup-smoke-voice.txt', 'custom-file.txt')
foreach ($marker in $markers) {
    Set-Content -LiteralPath (Join-Path $install $marker) -Value 'Preserve this file' -Encoding ascii
}
Run-Package $setupPath $arguments
& $Python tools/smoke_frozen.py $executable --hostile-qt-env
if ($LASTEXITCODE -ne 0) { throw 'Installed executable did not pass native smoke' }

$uninstaller = @(Get-ChildItem -LiteralPath $install -Filter 'unins*.exe' -File)
if ($uninstaller.Count -ne 1) { throw 'Expected one registered uninstaller' }
Run-Package $uninstaller[0].FullName $silent
foreach ($marker in $markers) {
    if ((Get-Content -LiteralPath (Join-Path $install $marker) -Raw).Trim() -ne 'Preserve this file') {
        throw "Installer changed a user-created file: $marker"
    }
}
foreach ($removed in @($executable, $startMenu, $desktop, $uninstallKey)) {
    if (Test-Path -LiteralPath $removed) { throw "Uninstaller left an installed entry: $removed" }
}
Write-Output 'Installation, custom location, shortcuts, upgrade, data preservation and uninstall passed.'
