# Keep the SSH tunnel independent of this launcher window.
param([string]$ServerAddress = '150.158.19.232', [int]$LocalPort = 8790)
$ErrorActionPreference = 'Stop'
$privateKey = Join-Path $env:USERPROFILE '.ssh\stock2027_server_key'
$consoleUrl = "http://127.0.0.1:$LocalPort"
function Test-Console {
    try {
        $health = Invoke-RestMethod "$consoleUrl/healthz" -TimeoutSec 2
        return $health.ok -eq $true
    } catch { return $false }
}
if (-not (Test-Console)) {
    if (-not (Test-Path -LiteralPath $privateKey)) { throw 'SSH 私钥文件不存在。' }
    $sshArguments = @('-i', ('"' + $privateKey + '"'), '-p', '22', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=3', '-N', '-L', "${LocalPort}:127.0.0.1:8790", "ubuntu@$ServerAddress")
    $tunnel = Start-Process -FilePath (Get-Command ssh.exe).Source -ArgumentList $sshArguments -WindowStyle Hidden -PassThru
    $ready = $false
    for ($attempt = 0; $attempt -lt 15; $attempt++) {
        if (Test-Console) { $ready = $true; break }
        $tunnel.Refresh()
        if ($tunnel.HasExited) { break }
        Start-Sleep -Milliseconds 300
    }
    if (-not $ready) { throw '通道建立失败，请检查网络和 SSH 密钥。' }
}
Write-Host "工作台已连接：$consoleUrl"
Write-Host '可以关闭此窗口。若网络断开后无法访问，重新运行本脚本即可。'
