<#
.SYNOPSIS
    Installation de JARVIS en une commande sur Windows (fork LordEris/OpenJarvis).

.DESCRIPTION
    Usage (PowerShell, pas besoin d'etre administrateur) :

        irm https://raw.githubusercontent.com/LordEris/OpenJarvis/main/deploy/windows/setup-jarvis.ps1 | iex

    Etapes :
      1. installe Git si besoin (winget) et clone ce fork dans
         %LOCALAPPDATA%\OpenJarvis\src (ou le met a jour) ;
      2. propose d'installer les outils de compilation Microsoft (MSVC),
         indispensables au moteur Rust d'OpenJarvis ;
      3. lance l'installeur officiel deploy\windows\install.ps1 (Python, uv,
         Ollama, modele de depart, commande `jarvis`) ;
      4. cree un environnement Python dedie au terminal avec la voix locale
         (Kokoro + faster-whisper), la recherche web et la lecture de PDF.
         Il est separe de celui de l'app de bureau, qui re-synchronise le
         sien a chaque demarrage et effacerait ces extras ;
      5. installe le preset "all-in-one" en francais et choisit le modele
         adapte a la RAM ;
      6. installe l'app de bureau OpenJarvis et la relie a ce fork.

    Relancer la commande est sans danger : elle met a jour au lieu de tout refaire.

    Options (variables d'environnement, car `irm | iex` ne passe pas d'arguments) :
      $env:JARVIS_NO_APP      = '1'   ne pas installer l'app de bureau
      $env:JARVIS_KEEP_CONFIG = '1'   garder la config existante
      $env:JARVIS_ANALYTICS   = '1'   accepter les statistiques d'usage anonymes
      $env:OPENJARVIS_REPO_URL        depot a installer (defaut : le fork LordEris)
      $env:OPENJARVIS_BRANCH          branche (defaut : main)
      $env:OPENJARVIS_HOME            dossier d'installation

.NOTES
    Fichier volontairement en ASCII : Windows PowerShell 5.1 lit les scripts
    sans BOM en ANSI, ce qui abimerait les accents.
#>

function Invoke-JarvisSetup {
    # 'Continue' et pas 'Stop' : sous Windows PowerShell 5.1, une commande
    # native qui ecrit sur stderr (uv, git, jarvis) avec `2>$null` deviendrait
    # une erreur fatale. Chaque etape verifie donc $LASTEXITCODE elle-meme, et
    # les cmdlets importantes ont -ErrorAction Stop.
    $ErrorActionPreference = 'Continue'

    function Write-Step ($msg) { Write-Host ""; Write-Host "> $msg" -ForegroundColor White }
    function Write-Info ($msg) { Write-Host "  . $msg" -ForegroundColor Cyan }
    function Write-Ok   ($msg) { Write-Host "  v $msg" -ForegroundColor Green }
    function Write-Warn ($msg) { Write-Host "  ! $msg" -ForegroundColor Yellow }
    # `throw` et pas `exit` : sous `irm | iex`, exit fermerait la fenetre PowerShell.
    function Stop-Setup ($msg) { throw "JARVIS : $msg" }

    function Update-PathFromRegistry {
        $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
        $user    = [Environment]::GetEnvironmentVariable('Path', 'User')
        $env:Path = [Environment]::ExpandEnvironmentVariables("$machine;$user")
    }

    function Test-Interactive {
        return [Environment]::UserInteractive -and -not [Console]::IsInputRedirected
    }

    function Test-MsvcTools {
        if (-not ${env:ProgramFiles(x86)}) { return $false }
        $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
        if (-not (Test-Path -LiteralPath $vswhere)) { return $false }
        $found = & $vswhere -latest -products * `
            -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
            -property installationPath 2>$null
        return ($LASTEXITCODE -eq 0 -and $found)
    }

    $repoUrl = if ($env:OPENJARVIS_REPO_URL) { $env:OPENJARVIS_REPO_URL } else { 'https://github.com/LordEris/OpenJarvis.git' }
    $branch  = if ($env:OPENJARVIS_BRANCH) { $env:OPENJARVIS_BRANCH } else { 'main' }
    $installRoot = if ($env:OPENJARVIS_HOME) { $env:OPENJARVIS_HOME } else { Join-Path $env:LOCALAPPDATA 'OpenJarvis' }
    $srcDir  = Join-Path $installRoot 'src'
    $cliVenv = Join-Path $installRoot '.venv-cli'
    # Meme regle que openjarvis.core.paths.get_config_dir().
    $configDir = if ($env:OPENJARVIS_HOME) { $env:OPENJARVIS_HOME }
                 elseif ($env:XDG_DATA_HOME) { Join-Path $env:XDG_DATA_HOME 'openjarvis' }
                 else { Join-Path $HOME '.openjarvis' }
    $analyticsFlag = if ($env:JARVIS_ANALYTICS -eq '1') { '--analytics' } else { '--no-analytics' }
    $extras = @('--extra', 'desktop', '--extra', 'voice', '--extra', 'speech',
                '--extra', 'tools-search', '--extra', 'pdf', '--group', 'desktop-native')

    Write-Host ""
    Write-Host "  +----------------------------------------+" -ForegroundColor White
    Write-Host "  |   J.A.R.V.I.S - installation (Windows) |" -ForegroundColor White
    Write-Host "  +----------------------------------------+" -ForegroundColor White

    # -- 0. Verifications ---------------------------------------------------
    if ($PSVersionTable.Platform -and $PSVersionTable.Platform -ne 'Win32NT') {
        Stop-Setup "ce script est pour Windows. Sous Linux : scripts/setup-jarvis.sh"
    }

    # -- 1. Git + code source ----------------------------------------------
    Write-Step "Code source ($repoUrl, branche $branch)"
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            Stop-Setup "Git est introuvable et winget aussi. Installe Git depuis https://git-scm.com puis relance."
        }
        Write-Info "installation de Git (winget)..."
        & winget install --id Git.Git --silent --accept-source-agreements --accept-package-agreements | Out-Null
        Update-PathFromRegistry
        if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
            Stop-Setup "Git installe mais introuvable : ouvre un nouveau PowerShell et relance."
        }
    }
    if (-not (Test-Path $installRoot)) { New-Item -ItemType Directory -Path $installRoot -ErrorAction Stop | Out-Null }

    if (Test-Path (Join-Path $srcDir '.git')) {
        $current = (& git -C $srcDir remote get-url origin 2>$null)
        if ($current -ne $repoUrl) {
            Write-Info "origin passe de $current a $repoUrl"
            & git -C $srcDir remote set-url origin $repoUrl
        }
        & git -C $srcDir fetch --tags origin $branch
        if ($LASTEXITCODE -ne 0) { Stop-Setup "git fetch a echoue (connexion ?)" }
        & git -C $srcDir show-ref --verify --quiet "refs/heads/$branch"
        if ($LASTEXITCODE -eq 0) { & git -C $srcDir checkout $branch }
        else { & git -C $srcDir checkout -b $branch "origin/$branch" }
        if ($LASTEXITCODE -ne 0) { Stop-Setup "impossible de passer sur la branche $branch" }
        & git -C $srcDir merge --ff-only "origin/$branch"
        if ($LASTEXITCODE -ne 0) {
            Stop-Setup "des modifications locales dans $srcDir bloquent la mise a jour (git status), regle-les puis relance."
        }
    } else {
        # Depot local : pas de clone partiel (git ne sait pas filtrer en local).
        if ($repoUrl -like 'file://*' -or (Test-Path -LiteralPath $repoUrl -ErrorAction SilentlyContinue)) {
            & git clone --branch $branch $repoUrl $srcDir
        } else {
            & git clone --filter=blob:none --branch $branch $repoUrl $srcDir
        }
        if ($LASTEXITCODE -ne 0) { Stop-Setup "git clone a echoue" }
    }
    Write-Ok "code dans $srcDir"

    # -- 2. Outils de compilation Microsoft --------------------------------
    Write-Step "Outils de compilation Microsoft (MSVC)"
    if (Test-MsvcTools) {
        Write-Ok "deja presents"
    } else {
        Write-Warn "Le moteur Rust d'OpenJarvis a besoin des 'Build Tools for Visual Studio'."
        Write-Warn "Telechargement de plusieurs Go et une fenetre d'autorisation Windows (UAC)."
        $answer = 'o'
        if (Test-Interactive) { $answer = Read-Host "  Les installer maintenant ? [O/n]" }
        if ($answer -match '^[nN]') {
            Stop-Setup "installe-les puis relance : winget install --id Microsoft.VisualStudio.2022.BuildTools --override `"--wait --quiet --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended`""
        }
        & winget install --id Microsoft.VisualStudio.2022.BuildTools --silent `
            --accept-source-agreements --accept-package-agreements `
            --override "--wait --quiet --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
        if (-not (Test-MsvcTools)) {
            Stop-Setup "installation des Build Tools non confirmee. Redemarre le PC si Windows le demande, puis relance."
        }
        Write-Ok "Build Tools installes"
    }

    # -- 3. Installeur officiel --------------------------------------------
    Write-Step "Installeur OpenJarvis (Python, uv, Ollama, modele de depart)"
    $psExe = (Get-Process -Id $PID).Path
    $env:OPENJARVIS_REPO_URL = $repoUrl
    & $psExe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $srcDir 'deploy\windows\install.ps1')
    if ($LASTEXITCODE -ne 0) { Stop-Setup "l'installeur officiel a echoue (voir les messages au-dessus)" }
    Update-PathFromRegistry
    $uvExe = (Get-Command uv -ErrorAction SilentlyContinue).Source
    if (-not $uvExe) { Stop-Setup "uv introuvable apres l'installation : ouvre un nouveau PowerShell et relance." }

    # -- 4. Environnement du terminal : voix, recherche, PDF ----------------
    Write-Step "Voix locale, recherche web, PDF (environnement du terminal)"
    $env:UV_PROJECT_ENVIRONMENT = $cliVenv
    Push-Location $srcDir
    try {
        & $uvExe sync --no-dev @extras
        if ($LASTEXITCODE -ne 0) { Stop-Setup "uv sync a echoue (voir au-dessus)" }
    } finally {
        Pop-Location
    }
    # Le raccourci `jarvis` utilise cet environnement (et non celui de l'app).
    $shimPath = Join-Path $installRoot 'bin\jarvis.cmd'
    $shim = @"
@echo off
setlocal
set "SRC=%~dp0..\src"
set "UV_PROJECT_ENVIRONMENT=%~dp0..\.venv-cli"
uv run --project "%SRC%" --no-dev $($extras -join ' ') jarvis %*
"@
    Set-Content -Path $shimPath -Value $shim -Encoding ASCII -ErrorAction Stop
    Write-Ok "commande jarvis prete ($cliVenv)"

    function Invoke-Jarvis {
        & $uvExe run --project $srcDir --no-dev @extras jarvis @args
    }

    # -- 5. Persona francaise + configuration tout-en-un -------------------
    Write-Step "Configuration en francais"
    $personaDir = Join-Path $configDir 'prompts\personas'
    New-Item -ItemType Directory -Force -Path $personaDir -ErrorAction Stop | Out-Null
    Copy-Item -Force -ErrorAction Stop (Join-Path $srcDir 'configs\openjarvis\prompts\personas\jarvis-fr.md') $personaDir

    $configPath = Join-Path $configDir 'config.toml'
    $wroteConfig = $false
    if ($env:JARVIS_KEEP_CONFIG -eq '1' -and (Test-Path $configPath)) {
        Write-Info "config existante conservee ($configPath)"
    } else {
        if (Test-Path $configPath) {
            $backup = "$configPath.avant-setup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
            Copy-Item $configPath $backup -ErrorAction Stop
            Write-Info "ancienne config sauvegardee dans $backup"
        }
        Invoke-Jarvis --quiet init --preset all-in-one --force $analyticsFlag | Out-Null
        if ($LASTEXITCODE -ne 0) { Stop-Setup "jarvis init --preset all-in-one a echoue" }
        $wroteConfig = $true
        Write-Ok "preset all-in-one installe"
    }

    $model = (& $uvExe run --project $srcDir --no-dev @extras python -c `
        "from openjarvis.core.config import detect_hardware, recommend_model; print(recommend_model(detect_hardware(), 'ollama') or '')" 2>$null)
    if (-not $model) { $model = 'qwen3.5:4b' }
    $model = "$model".Trim()
    if ($wroteConfig) {
        Invoke-Jarvis config set intelligence.default_model $model 2>$null | Out-Null
    }
    Write-Ok "modele choisi pour ta machine : $model"

    $ollamaExe = (Get-Command ollama -ErrorAction SilentlyContinue).Source
    if ($ollamaExe) {
        Write-Info "telechargement de $model (plusieurs Go la premiere fois, patience)..."
        & $ollamaExe pull $model
        if ($LASTEXITCODE -eq 0) { Write-Ok "$model pret" }
        else { Write-Warn "telechargement interrompu : relance 'ollama pull $model' plus tard" }
    } else {
        Write-Warn "Ollama introuvable : relance ce script dans un nouveau PowerShell"
    }

    # -- 6. App de bureau reliee a ce fork ---------------------------------
    # L'app cherche le code source via OPENJARVIS_ROOT.
    [Environment]::SetEnvironmentVariable('OPENJARVIS_ROOT', $srcDir, 'User')
    $env:OPENJARVIS_ROOT = $srcDir
    $appInstalled = $false
    if ($env:JARVIS_NO_APP -ne '1') {
        Write-Step "App de bureau"
        $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'x64' }
        $setupExe = Join-Path $env:TEMP "OpenJarvis_$arch-setup.exe"
        try {
            Write-Info "telechargement de l'app ($arch)..."
            Invoke-WebRequest -UseBasicParsing -ErrorAction Stop -OutFile $setupExe `
                -Uri "https://github.com/open-jarvis/OpenJarvis/releases/latest/download/OpenJarvis_$arch-setup.exe"
            $proc = Start-Process -FilePath $setupExe -ArgumentList '/S' -Wait -PassThru -ErrorAction Stop
            if ($proc.ExitCode -ne 0) { throw "installeur de l'app : code $($proc.ExitCode)" }
            $appInstalled = $true
            Write-Ok "app 'OpenJarvis' installee (menu Demarrer)"
        } catch {
            Write-Warn "app non installee : telecharge-la sur https://github.com/open-jarvis/OpenJarvis/releases/latest"
        } finally {
            Remove-Item $setupExe -ErrorAction SilentlyContinue
        }
    }

    # -- Fin ---------------------------------------------------------------
    Write-Host ""
    Write-Host "  +----------------------------------------+" -ForegroundColor Green
    Write-Host "  |      JARVIS est pret, monsieur.        |" -ForegroundColor Green
    Write-Host "  +----------------------------------------+" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Modele : $model (100 % local, via Ollama)"
    Write-Host ""
    if ($appInstalled) {
        Write-Host "  - L'app : menu Demarrer -> 'OpenJarvis'"
    }
    Write-Host "  - Dans un NOUVEAU PowerShell :"
    Write-Host "      jarvis                 discuter"
    Write-Host "      jarvis gui             interface dans le navigateur"
    Write-Host "      jarvis doctor          verifier que tout va bien"
    Write-Host "      jarvis connect gdrive  brancher Gmail / Agenda pour le briefing"
    Write-Host "      jarvis digest --fresh  ecouter le briefing du matin"
    Write-Host ""
}

# Sous `irm | iex`, le script tourne dans la session de l'utilisateur : on
# remet les variables d'environnement modifiees en place a la fin, sinon
# UV_PROJECT_ENVIRONMENT detournerait ses autres projets uv.
& {
    $savedUvEnv = $env:UV_PROJECT_ENVIRONMENT
    $savedRepo  = $env:OPENJARVIS_REPO_URL
    try {
        Invoke-JarvisSetup
    } finally {
        $env:UV_PROJECT_ENVIRONMENT = $savedUvEnv
        $env:OPENJARVIS_REPO_URL    = $savedRepo
    }
}
