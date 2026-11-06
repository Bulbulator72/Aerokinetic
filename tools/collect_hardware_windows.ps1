param([string]$ReportPath = "")
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ([string]::IsNullOrWhiteSpace($ReportPath)) {
    $ReportPath = Join-Path (Split-Path $PSScriptRoot -Parent) "hardware_windows.json"
}

$systemInfo = Get-CimInstance Win32_ComputerSystem
$osInfo = Get-CimInstance Win32_OperatingSystem
$cpuInfo = @(Get-CimInstance Win32_Processor | ForEach-Object {
    [ordered]@{
        name = $_.Name.Trim()
        physical_cores = $_.NumberOfCores
        logical_processors = $_.NumberOfLogicalProcessors
    }
})
$notes = @()
$gpuDevices = @()
$nvidiaCommand = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
if ($null -ne $nvidiaCommand) {
    try {
        $nvidiaExecutable = $nvidiaCommand.Source
        $gpuLines = @(& $nvidiaExecutable '--query-gpu=name,memory.total,memory.free,driver_version' '--format=csv,noheader,nounits' 2>$null)
        if ($LASTEXITCODE -eq 0) {
            $gpuDevices = @($gpuLines | ConvertFrom-Csv -Header gpu_name,total_mib,free_mib,driver_version | ForEach-Object {
                [ordered]@{
                    name = $_.gpu_name.Trim()
                    memory_total_mib = [double]::Parse($_.total_mib.Trim(), [Globalization.CultureInfo]::InvariantCulture)
                    memory_free_mib = [double]::Parse($_.free_mib.Trim(), [Globalization.CultureInfo]::InvariantCulture)
                    driver_version = $_.driver_version.Trim()
                }
            })
        } else {
            $notes += 'nvidia-smi was found but did not return GPU information.'
        }
    } catch {
        $gpuDevices = @()
        $notes += 'NVIDIA query failed or returned unavailable memory values. GPU memory remains unverified.'
    }
} else {
    $notes += 'nvidia-smi.exe was not found. GPU name and dedicated memory remain unverified.'
}

$driveRoot = [IO.Path]::GetPathRoot((Split-Path $PSScriptRoot -Parent))
$driveId = $driveRoot.TrimEnd('\')
$diskInfo = $null
if ($driveId -match '^[A-Za-z]:$') {
    $disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$driveId'"
    if ($null -ne $disk) {
        $diskInfo = [ordered]@{drive = $driveId; total_bytes = $disk.Size; free_bytes = $disk.FreeSpace}
    }
}

$report = [ordered]@{
    captured_at_utc = [DateTime]::UtcNow.ToString('o')
    os = [ordered]@{name = $osInfo.Caption; version = $osInfo.Version; architecture = $osInfo.OSArchitecture}
    cpu = $cpuInfo
    memory = [ordered]@{total_bytes = $systemInfo.TotalPhysicalMemory; free_bytes = [uint64]$osInfo.FreePhysicalMemory * 1024}
    nvidia_gpu = $gpuDevices
    project_disk = $diskInfo
    notes = $notes
    diagnostic_only = $true
    solver_benchmark_performed = $false
}
$json = $report | ConvertTo-Json -Depth 6
$reportDirectory = Split-Path ([IO.Path]::GetFullPath($ReportPath)) -Parent
[IO.Directory]::CreateDirectory($reportDirectory) | Out-Null
[IO.File]::WriteAllText($ReportPath, $json, [Text.UTF8Encoding]::new($false))
Write-Output $json
Write-Output "Saved hardware report: $ReportPath"
