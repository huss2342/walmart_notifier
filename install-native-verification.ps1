# Current-user, on-demand Chrome Native Messaging host. No persistent process.
[CmdletBinding(SupportsShouldProcess = $true)]
param([switch]$Remove)

$ErrorActionPreference = 'Stop'

function Test-OwnedNativeRegistration {
    param([string]$RegisteredPath, [string]$ExpectedManifest, [string]$ExpectedLauncher)
    try {
        if ([IO.Path]::GetFullPath($RegisteredPath) -ne [IO.Path]::GetFullPath($ExpectedManifest)) { return $false }
        $nativeDocument = Get-Content -LiteralPath $ExpectedManifest -Raw | ConvertFrom-Json
        return (
            $nativeDocument.name -eq 'com.reviewer_item_relay.verification' -and
            $nativeDocument.type -eq 'stdio' -and
            [IO.Path]::GetFullPath([string]$nativeDocument.path) -eq [IO.Path]::GetFullPath($ExpectedLauncher) -and
            @($nativeDocument.allowed_origins).Count -eq 1 -and
            $nativeDocument.allowed_origins[0] -ceq 'chrome-extension://oidgbnehgnikmniolojjkmhlpifdoiil/'
        )
    } catch { return $false }
}

# Dot sourcing exposes only the pure ownership check for tests and maintenance.
if ($MyInvocation.InvocationName -eq '.') { return }
if ($env:OS -ne 'Windows_NT') { throw 'This helper requires an interactive Windows account.' }

$nativeRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$nativeFolder = [IO.Path]::GetFullPath((Join-Path $nativeRoot 'data\native-verification'))
$nativeManifest = Join-Path $nativeFolder 'host.json'
$nativeLauncher = Join-Path $nativeFolder 'launcher.exe'
$nativeRegistry = 'HKCU:\Software\Google\Chrome\NativeMessagingHosts\com.reviewer_item_relay.verification'
$nativeRegistered = $null
if (Test-Path -LiteralPath $nativeRegistry) {
    $nativeRegistered = (Get-Item -LiteralPath $nativeRegistry).GetValue('')
    if (-not (Test-OwnedNativeRegistration -RegisteredPath $nativeRegistered -ExpectedManifest $nativeManifest -ExpectedLauncher $nativeLauncher)) {
        throw 'An existing native host is owned by another project or has unexpected contents; it was left unchanged.'
    }
}
if ((Test-Path -LiteralPath $nativeManifest) -and -not (Test-OwnedNativeRegistration -RegisteredPath $nativeManifest -ExpectedManifest $nativeManifest -ExpectedLauncher $nativeLauncher)) {
    throw 'The generated manifest path contains unexpected data; it was left unchanged.'
}

if ($Remove) {
    if ($nativeRegistered -and $PSCmdlet.ShouldProcess($nativeManifest, 'Remove this project native host registration and generated launcher')) {
        # Every removal target is absolute and confined to these exact filenames.
        if ([IO.Path]::GetDirectoryName($nativeManifest) -ne $nativeFolder -or [IO.Path]::GetDirectoryName($nativeLauncher) -ne $nativeFolder) {
            throw 'Native helper removal paths are outside the expected project folder.'
        }
        Remove-Item -LiteralPath $nativeRegistry
        foreach ($nativeFile in @($nativeManifest, $nativeLauncher)) {
            if (Test-Path -LiteralPath $nativeFile) { Remove-Item -LiteralPath $nativeFile }
        }
        Write-Output 'Removed this project native host. Source and diagnostic logs were retained.'
    }
    return
}

$nativePython = Join-Path $nativeRoot '.venv\Scripts\python.exe'
$nativeHost = Join-Path $nativeRoot 'native_verification\host.py'
$nativeSource = Join-Path $nativeRoot 'native_verification\launcher.cs'
foreach ($nativeRequired in @($nativePython, $nativeHost, $nativeSource)) {
    if (-not (Test-Path -LiteralPath $nativeRequired -PathType Leaf)) { throw 'Create the project .venv and preserve the native helper source before installing.' }
}
$nativeCompiler = @(
    (Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'),
    (Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe')
) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $nativeCompiler) { throw 'The installed Windows .NET C# compiler was not found; nothing was downloaded.' }

if ($PSCmdlet.ShouldProcess($nativeManifest, 'Compile and register current-user Chrome native verification fallback')) {
    [IO.Directory]::CreateDirectory($nativeFolder) | Out-Null
    # Compile to a unique staging file; replace only after successful compilation.
    $nativeStaging = Join-Path $nativeFolder ('launcher-' + [Guid]::NewGuid().ToString('N') + '.exe')
    try {
        & $nativeCompiler /nologo /target:exe /optimize+ "/out:$nativeStaging" $nativeSource
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $nativeStaging -PathType Leaf)) { throw 'Local native launcher compilation failed.' }
        Move-Item -LiteralPath $nativeStaging -Destination $nativeLauncher -Force
    } finally {
        if (Test-Path -LiteralPath $nativeStaging) { Remove-Item -LiteralPath $nativeStaging }
    }
    $nativeDocument = [ordered]@{
        name = 'com.reviewer_item_relay.verification'
        description = 'Reviewer Item Relay bounded Windows verification input'
        path = $nativeLauncher
        type = 'stdio'
        allowed_origins = @('chrome-extension://oidgbnehgnikmniolojjkmhlpifdoiil/')
    }
    [IO.File]::WriteAllText($nativeManifest, ($nativeDocument | ConvertTo-Json -Depth 4), [Text.UTF8Encoding]::new($false))
    New-Item -Path $nativeRegistry -Force | Out-Null
    Set-Item -LiteralPath $nativeRegistry -Value $nativeManifest
    Write-Output 'Installed current-user native host. Enable Windows fallback in Reviewer Item Relay Options. Remove with .\install-native-verification.ps1 -Remove.'
}
