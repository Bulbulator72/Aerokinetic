$ErrorActionPreference = 'Stop'
$self = $env:AK_RESEARCH_SELF
$raw = [IO.File]::ReadAllText($self, [Text.Encoding]::UTF8)
$match = [regex]::Match($raw, '(?ms)^AK_RESEARCH_DATA_BEGIN\r?\n(?<data>.*?)^AK_RESEARCH_DATA_END\r?$')
if (-not $match.Success) { throw 'Save the complete test BAT/TXT, including the source payload.' }
$json = $match.Groups['data'].Value.Trim()
$bytes = [Text.Encoding]::UTF8.GetBytes($json)
$sha = [Security.Cryptography.SHA256]::Create()
try { $hash = ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant() }
finally { $sha.Dispose() }
if ($hash -ne '__PAYLOAD_SHA256__') { throw 'Test file SHA-256 differs. Save the complete file again.' }
$payload = ConvertFrom-Json -InputObject $json
$program = [IO.Path]::GetDirectoryName($self)
$root = [IO.Path]::Combine($program, 'AEROKINETIC_TESTS_053')
$kit = [IO.Path]::Combine($root, 'kit')
$null = [IO.Directory]::CreateDirectory($root)
$utf8 = New-Object System.Text.UTF8Encoding -ArgumentList $false

function Get-Hash([byte[]] $Data) {
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($algorithm.ComputeHash($Data))).Replace('-', '').ToLowerInvariant() }
    finally { $algorithm.Dispose() }
}
function Get-SafePath([string] $Base, [string] $Relative) {
    if ([IO.Path]::IsPathRooted($Relative)) { throw 'Absolute source member is forbidden.' }
    $path = [IO.Path]::GetFullPath([IO.Path]::Combine($Base, $Relative.Replace('/', '\')))
    $prefix = [IO.Path]::GetFullPath($Base).TrimEnd('\') + '\'
    if (-not $path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Source member escapes kit.' }
    return $path
}
if (-not [IO.Directory]::Exists($kit)) {
    $staging = [IO.Path]::Combine($root, '.kit_' + [Guid]::NewGuid().ToString('N'))
    $null = [IO.Directory]::CreateDirectory($staging)
    foreach ($property in $payload.files.PSObject.Properties) {
        $file = Get-SafePath $staging $property.Name
        $data = [Text.Encoding]::UTF8.GetBytes([string] $property.Value.content)
        if ($data.Length -ne $property.Value.bytes -or (Get-Hash $data) -ne $property.Value.sha256) { throw ('Damaged source: ' + $property.Name) }
        $null = [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($file))
        [IO.File]::WriteAllBytes($file, $data)
    }
    [IO.File]::WriteAllText([IO.Path]::Combine($staging, 'KIT_PAYLOAD_SHA256.txt'), $hash, $utf8)
    [IO.Directory]::Move($staging, $kit)
}
if ([IO.File]::ReadAllText([IO.Path]::Combine($kit, 'KIT_PAYLOAD_SHA256.txt')).Trim() -ne $hash) {
    throw 'Another test kit is already present. Rename AEROKINETIC_TESTS_053 to preserve its results, then run again.'
}
foreach ($property in $payload.files.PSObject.Properties) {
    $file = Get-SafePath $kit $property.Name
    if (-not [IO.File]::Exists($file) -or (Get-Hash ([IO.File]::ReadAllBytes($file))) -ne $property.Value.sha256) {
        throw ('Missing or changed test source: ' + $property.Name)
    }
}
$null = [IO.Directory]::CreateDirectory([IO.Path]::Combine($root, 'study'))
$stop = '@echo off' + "`r`n" + 'if not exist "%~dp0study" mkdir "%~dp0study"' + "`r`n" +
        '> "%~dp0study\STOP_REQUESTED" echo stop' + "`r`n" +
        'echo Stop requested. Wait for the calculation window to save its checkpoint.' + "`r`n" + 'pause' + "`r`n"
[IO.File]::WriteAllText([IO.Path]::Combine($root, 'STOP_TESTS.bat'), $stop, [Text.Encoding]::ASCII)

$candidates = @()
if ($env:AK_TEST_PYTHON) { $candidates += $env:AK_TEST_PYTHON }
$candidates += [IO.Path]::Combine($program, '.venv\Scripts\python.exe')
$parent = [IO.Directory]::GetParent($program)
if ($parent) { $candidates += [IO.Path]::Combine($parent.FullName, '.venv\Scripts\python.exe') }
$candidates += @(
    [IO.Path]::Combine($env:LOCALAPPDATA, 'Programs\Python\Python312\python.exe'),
    [IO.Path]::Combine($env:ProgramFiles, 'Python312\python.exe'), 'C:\Python312\python.exe')
$py = $null
foreach ($candidate in $candidates) {
    if (-not $candidate -or -not [IO.File]::Exists($candidate)) { continue }
    & $candidate -c "import sys,struct; raise SystemExit(0 if sys.version_info[:2]==(3,12) and struct.calcsize('P')==8 else 2)"
    if ($LASTEXITCODE -eq 0) { $py = $candidate; break }
}
if (-not $py) {
    [IO.File]::WriteAllText([IO.Path]::Combine($root, 'PYTHON_NOT_FOUND.txt'),
        'Python 3.12 x64 was not found. Place this BAT beside the original .venv folder, or set AK_TEST_PYTHON to its python.exe. An older frozen AeroKinetic.exe alone cannot run the new research core. No download or system installation was attempted.', $utf8)
    throw ('Python 3.12 x64 not found. See ' + [IO.Path]::Combine($root, 'PYTHON_NOT_FOUND.txt'))
}
$threads = $env:AK_TEST_THREADS
if (-not $threads -or $threads -eq 'auto') { $threads = [Math]::Min(8, [Math]::Max(1, [Environment]::ProcessorCount)) }
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:OPENBLAS_NUM_THREADS = '1'
$env:OMP_WAIT_POLICY = 'PASSIVE'
$env:PYTHONPATH = [IO.Path]::Combine($kit, 'guard')
$env:AEROKINETIC_NETWORK_AUDIT = [IO.Path]::Combine($root, 'network_audit.jsonl')
Write-Host ('Python: ' + $py)
Write-Host ('CPU threads: ' + $threads + '; grids: ' + $env:AK_TEST_GRIDS + '; target tU/c: ' + $env:AK_TEST_DURATION)
Write-Host ('Results and review archive: ' + $root)
Write-Host ('To pause safely, run: ' + [IO.Path]::Combine($root, 'STOP_TESTS.bat'))
& $py -u ([IO.Path]::Combine($kit, 'bootstrap.py')) --kit $kit --workspace $root --program-root $program `
    --threads $threads --grids $env:AK_TEST_GRIDS --duration $env:AK_TEST_DURATION
exit $LASTEXITCODE
