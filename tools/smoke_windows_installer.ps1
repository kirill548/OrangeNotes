param(
    [Parameter(Mandatory)] [string] $Setup,
    [string] $Python = 'python',
    [switch] $AllUsers
)

$ErrorActionPreference = 'Stop'
$setupPath = (Resolve-Path -LiteralPath $Setup).Path
$scratch = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { $env:TEMP }
$root = Join-Path $scratch ('Orange Notes setup check ' + [Guid]::NewGuid().ToString('N'))
$install = if ($AllUsers) { Join-Path $root 'Orange Notes' } else { $root }
$executable = Join-Path $install 'OrangeNotes.exe'
$silent = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/NOCLOSEAPPLICATIONS')
$arguments = $silent + @(('/DIR="' + $install + '"'), '/TASKS=desktopicon')
if (!$AllUsers) { $arguments += '/CURRENTUSER' }
$programsFolder = if ($AllUsers) { 'CommonPrograms' } else { 'Programs' }
$desktopFolder = if ($AllUsers) { 'CommonDesktopDirectory' } else { 'DesktopDirectory' }
$hive = if ($AllUsers) { 'HKLM' } else { 'HKCU' }
$startMenu = Join-Path ([Environment]::GetFolderPath($programsFolder)) 'Orange Notes.lnk'
$desktop = Join-Path ([Environment]::GetFolderPath($desktopFolder)) 'Orange Notes.lnk'
$uninstallKey = "${hive}:\Software\Microsoft\Windows\CurrentVersion\Uninstall\OrangeNotes.Desktop.Windows_is1"

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

if ($AllUsers) {
    [void][IO.Directory]::CreateDirectory($root)
    $acl = Get-Acl -LiteralPath $root
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($identity in @('S-1-5-32-544', 'S-1-5-18')) {
        $sid = [Security.Principal.SecurityIdentifier]::new($identity)
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            $sid, 'FullControl', 'ContainerInherit, ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $root -AclObject $acl
} else {
    $protected = "$install protected"
    $blocked = Join-Path $protected 'Orange Notes'
    $log = "$install denied.log"
    [void][IO.Directory]::CreateDirectory($protected)
    $original = Get-Acl -LiteralPath $protected
    $denied = Get-Acl -LiteralPath $protected
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $rule = [Security.AccessControl.FileSystemAccessRule]::new($sid, 'CreateDirectories, CreateFiles', 'Deny')
    $denied.AddAccessRule($rule)
    try {
        Set-Acl -LiteralPath $protected -AclObject $denied
        $blockedArgs = $silent + @('/CURRENTUSER', ('/DIR="' + $blocked + '"'), ('/LOG="' + $log + '"'))
        $process = Start-Process -FilePath $setupPath -ArgumentList $blockedArgs -Wait -PassThru -WindowStyle Hidden
        if ($process.ExitCode -eq 0 -or !(Select-String -LiteralPath $log -SimpleMatch 'Destination check rejected the installation folder:' -Quiet)) {
            throw 'Setup did not reject a protected folder before installing.'
        }
        if ((Test-Path -LiteralPath $uninstallKey) -or (Get-ChildItem -LiteralPath $protected -Force)) {
            throw 'The rejected install left files or an uninstall entry.'
        }
    } finally {
        Set-Acl -LiteralPath $protected -AclObject $original
        if (!(Get-ChildItem -LiteralPath $protected -Force)) { [IO.Directory]::Delete($protected) }
        if (Test-Path -LiteralPath $log) { Remove-Item -LiteralPath $log }
    }
}

Run-Package $setupPath $arguments
$registered = Get-ItemProperty -LiteralPath $uninstallKey
if ($registered.InstallLocation.TrimEnd('\') -ine $install) {
    throw 'The uninstall entry does not match the installation mode and custom location.'
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
