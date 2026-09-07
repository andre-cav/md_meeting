$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    $pythonCommand = Get-Command python -ErrorAction Stop
    $version = & $pythonCommand.Source -c "import sys; print(str(sys.version_info.major) + '.' + str(sys.version_info.minor))"
    if ($version -notin @('3.11', '3.12', '3.13')) {
        throw 'Use Python 3.11, 3.12 ou 3.13 de 64 bits. A captura ainda não suporta Python 3.14. Consulte README.md.'
    }
    & $pythonCommand.Source -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao criar ambiente virtual.' }
}
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Falha na instalação das dependências.' }
Write-Host 'Pronto. Abra iniciar.vbs com dois cliques.'
