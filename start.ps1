# PEO-BD — Start do servidor (mata porta 8000 se ocupada, reinicia limpo)

$porta = 8000
$linhas = netstat -ano | Select-String ":$porta "
if ($linhas) {
    $pid_antigo = ($linhas[0] -split '\s+')[-1]
    Write-Host "Liberando porta $porta (PID $pid_antigo)..."
    Stop-Process -Id $pid_antigo -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
}

Write-Host "Iniciando PEO-BD na porta $porta..."
Set-Location $PSScriptRoot
python -m uvicorn api.main:app --reload --host 0.0.0.0 --port $porta
