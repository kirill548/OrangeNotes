# Publish the iOS artifact of cross-platform Orange Notes; other platforms are managed separately.
# Requires PowerShell 7+, Git and GitHub CLI. Publishing is explicit; use -DryRun first.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')]
    [string]$Repository,
    [Parameter(Mandatory)][string]$IpaPath,
    [Parameter(Mandatory)][string]$NotesPath,
    [ValidatePattern('^v[0-9]+\.[0-9]+\.[0-9]+$')][string]$Tag = 'v1.0.0',
    [switch]$Draft,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
if ($Repository.StartsWith('USERNAME/')) { throw 'Replace USERNAME with the repository owner.' }
$ipa = Get-Item -LiteralPath $IpaPath
$notes = Get-Item -LiteralPath $NotesPath
if ($ipa.PSIsContainer -or $ipa.Name -cne 'OrangeNotes-iOS-unsigned.ipa' -or $ipa.Length -eq 0) {
    throw 'Expected a nonempty OrangeNotes-iOS-unsigned.ipa file.'
}
if ($notes.PSIsContainer -or $notes.Length -eq 0) { throw 'Release notes must be a nonempty file.' }
if ($ipa.FullName -eq $notes.FullName) { throw 'IPA and release notes must be different files.' }
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [IO.Compression.ZipFile]::OpenRead($ipa.FullName)
try {
    if ($null -eq $archive.GetEntry('Payload/Runner.app/Info.plist')) {
        throw 'IPA is missing Payload/Runner.app/Info.plist.'
    }
} finally { $archive.Dispose() }
$hash = (Get-FileHash -LiteralPath $ipa.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
$checksumPath = Join-Path $ipa.DirectoryName 'SHA256SUMS.txt'
if ($notes.FullName -eq $checksumPath) { throw 'Release notes cannot be the generated checksum file.' }
$releaseArguments = @('release', 'create', $Tag, $ipa.FullName, $checksumPath,
    '--repo', $Repository, '--verify-tag', '--title', "Orange Notes $Tag",
    '--notes-file', $notes.FullName)
if ($Draft) { $releaseArguments += '--draft' }
if ($DryRun) {
    Write-Output "Validated IPA structure. SHA256: $hash"
    Write-Output "Would create cross-platform Orange Notes release $Tag in $Repository with its iOS IPA, SHA256SUMS.txt and supplied notes."
    Write-Output 'Dry run: no files written and no GitHub request sent.'
    return
}
Get-Command git, gh -ErrorAction Stop | Out-Null
$tracked = & git ls-files --error-unmatch -- $ipa.FullName 2>$null
if ($LASTEXITCODE -eq 0) { throw 'IPA is tracked in Git. Remove the binary from source control first.' }
& gh auth status --hostname github.com
if ($LASTEXITCODE -ne 0) { throw 'GitHub CLI authentication is required.' }
[IO.File]::WriteAllText($checksumPath, "$hash  $($ipa.Name)`n", [Text.UTF8Encoding]::new($false))
& gh @releaseArguments
if ($LASTEXITCODE -ne 0) { throw 'Release creation failed. No tag or history was changed by this script.' }
