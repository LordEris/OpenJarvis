#!/usr/bin/env bash
# setup-jarvis.sh — installation de JARVIS en une commande (Linux : Zorin, Ubuntu, Debian…)
#
#   curl -fsSL https://raw.githubusercontent.com/LordEris/OpenJarvis/main/scripts/setup-jarvis.sh | bash
#
# Ce que fait le script, dans l'ordre :
#   1. clone ce fork dans ~/.openjarvis/src (ou le met à jour) ;
#   2. lance l'installeur officiel (uv, Python, Ollama, Rust en arrière-plan) ;
#   3. ajoute la voix locale (Kokoro + faster-whisper), le serveur web,
#      la recherche web et la lecture de PDF ;
#   4. installe le preset « all-in-one » en français et choisit le modèle
#      adapté à la RAM de la machine ;
#   5. installe l'app de bureau OpenJarvis et la relie à ce fork.
#
# Options :
#   --no-app        ne pas installer l'app de bureau
#   --keep-config   garder ~/.openjarvis/config.toml s'il existe déjà
#   --analytics     accepter les statistiques d'usage anonymes (désactivées sinon)
#   --minimal       ne pas télécharger le modèle maintenant (il se télécharge en arrière-plan)
#
# Variables d'environnement :
#   OPENJARVIS_REPO_URL  dépôt à installer (défaut : le fork LordEris/OpenJarvis)
#   OPENJARVIS_BRANCH    branche à installer (défaut : main)
#   OPENJARVIS_HOME      dossier d'installation (défaut : ~/.openjarvis)
#
# Relancer le script est sans danger : il met à jour au lieu de tout refaire.
# Tout le script est dans main() pour qu'un téléchargement interrompu
# n'exécute jamais un script à moitié reçu.

set -euo pipefail

main() {
    local repo_url="${OPENJARVIS_REPO_URL:-https://github.com/LordEris/OpenJarvis.git}"
    local branch="${OPENJARVIS_BRANCH:-main}"
    local home_dir="${OPENJARVIS_HOME:-$HOME/.openjarvis}"
    local src_dir="$home_dir/src"
    local venv_dir="$home_dir/.venv"

    local install_app=1 keep_config=0 minimal=0 analytics_flag="--no-analytics"
    local arg
    for arg in "$@"; do
        case "$arg" in
            --no-app) install_app=0 ;;
            --keep-config) keep_config=1 ;;
            --analytics) analytics_flag="--analytics" ;;
            --minimal) minimal=1 ;;
            -h|--help)
                echo "Options : --no-app  --keep-config  --analytics  --minimal"
                echo "Détails : en-tête de scripts/setup-jarvis.sh"
                return 0
                ;;
            *) fail "option inconnue : $arg (voir --help)" ;;
        esac
    done

    banner

    # ── 0. Vérifications ────────────────────────────────────────────────
    [[ "$(uname -s)" == "Linux" ]] \
        || fail "ce script est pour Linux. Sous Windows : deploy/windows/setup-jarvis.ps1"
    [[ "$(id -u)" -ne 0 ]] \
        || fail "ne lance pas ce script avec sudo : il s'installe dans ton dossier personnel et demandera sudo lui-même si besoin."
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

    # ── 1. Paquets système (git, voix, son) ─────────────────────────────
    step "Paquets système"
    local pkgs=()
    command -v git >/dev/null 2>&1 || pkgs+=(git)
    command -v curl >/dev/null 2>&1 || pkgs+=(curl)
    command -v espeak-ng >/dev/null 2>&1 || pkgs+=(espeak-ng)
    has_portaudio || pkgs+=(libportaudio2)
    if ((${#pkgs[@]})); then
        if command -v apt-get >/dev/null 2>&1; then
            info "installation de : ${pkgs[*]} (ton mot de passe peut être demandé)"
            sudo apt-get update -q || true
            sudo apt-get install -y "${pkgs[@]}"
        else
            warn "installe toi-même : ${pkgs[*]} (apt-get introuvable)"
            command -v git >/dev/null 2>&1 || fail "git est indispensable"
        fi
    fi
    ok "paquets système prêts"

    # ── 2. Code source depuis le fork ───────────────────────────────────
    step "Code source ($repo_url, branche $branch)"
    mkdir -p "$home_dir"
    if [[ -d "$src_dir/.git" ]]; then
        local current_url
        current_url="$(git -C "$src_dir" remote get-url origin 2>/dev/null || true)"
        if [[ "$current_url" != "$repo_url" ]]; then
            info "origin passe de $current_url à $repo_url"
            git -C "$src_dir" remote set-url origin "$repo_url"
        fi
        git -C "$src_dir" fetch --tags origin "$branch"
        if git -C "$src_dir" show-ref --verify --quiet "refs/heads/$branch"; then
            git -C "$src_dir" checkout "$branch"
        else
            git -C "$src_dir" checkout -b "$branch" "origin/$branch"
        fi
        git -C "$src_dir" merge --ff-only "origin/$branch" \
            || fail "des modifications locales dans $src_dir bloquent la mise à jour. Règle-les (git status) puis relance."
    elif [[ "$repo_url" == file://* || -e "$repo_url" ]]; then
        # Dépôt local : pas de clone partiel (git ne sait pas filtrer en local).
        git clone --branch "$branch" "$repo_url" "$src_dir"
    else
        git clone --filter=blob:none --branch "$branch" "$repo_url" "$src_dir"
    fi
    ok "code dans $src_dir"

    # ── 3. Installeur officiel (uv, venv, Ollama, modèle de départ) ─────
    step "Installeur OpenJarvis"
    local install_args=("$analytics_flag")
    ((minimal)) && install_args+=(--minimal)
    # L'installeur lit le clavier : on lui donne le terminal, pas ce script.
    OPENJARVIS_HOME="$home_dir" OPENJARVIS_REPO_URL="$repo_url" \
        bash "$src_dir/scripts/install/install.sh" "${install_args[@]}" < "$(tty_or_null)"
    [[ -x "$venv_dir/bin/jarvis" ]] || fail "l'installeur n'a pas créé $venv_dir/bin/jarvis"
    ok "OpenJarvis installé"

    # ── 4. Fonctions en plus : voix, serveur web, recherche, PDF ────────
    step "Voix locale, interface web, recherche web, PDF"
    # --torch-backend auto : PyTorch (requis par Kokoro) en version CPU si pas
    # de carte NVIDIA, soit ~3 Go de moins à télécharger.
    (cd "$src_dir" && uv pip install --python "$venv_dir/bin/python" \
        --torch-backend auto -e ".[server,voice,speech,tools-search,pdf]")
    ok "extras installés"

    # ── 5. Persona française et configuration tout-en-un ────────────────
    step "Configuration en français"
    mkdir -p "$home_dir/prompts/personas"
    cp -f "$src_dir/configs/openjarvis/prompts/personas/jarvis-fr.md" \
        "$home_dir/prompts/personas/jarvis-fr.md"

    local config="$home_dir/config.toml" wrote_config=0
    if ((keep_config)) && [[ -f "$config" ]]; then
        info "--keep-config : $config conservé"
    else
        wrote_config=1
        if [[ -f "$config" ]]; then
            local backup
            backup="$config.avant-setup-$(date +%Y%m%d-%H%M%S)"
            cp -f "$config" "$backup"
            info "ancienne config sauvegardée dans $backup"
        fi
        OPENJARVIS_HOME="$home_dir" "$venv_dir/bin/jarvis" --quiet init \
            --preset all-in-one --force "$analytics_flag" < /dev/null >/dev/null
        ok "preset all-in-one installé"
    fi

    # Modèle adapté à la RAM (2b / 4b / 9b / 27b).
    local model
    model="$(OPENJARVIS_HOME="$home_dir" "$venv_dir/bin/python" - <<'PY' 2>/dev/null || true
from openjarvis.core.config import detect_hardware, recommend_model
print(recommend_model(detect_hardware(), "ollama") or "")
PY
)"
    model="${model:-qwen3.5:4b}"
    if ((wrote_config)); then
        OPENJARVIS_HOME="$home_dir" "$venv_dir/bin/jarvis" config set \
            intelligence.default_model "$model" >/dev/null 2>&1 \
            || warn "impossible de régler le modèle, garde celui du preset"
    fi
    ok "modèle choisi pour ta machine : $model"

    if ! ((minimal)); then
        info "téléchargement de $model (plusieurs Go la première fois, patience)…"
        if ollama pull "$model"; then
            ok "$model prêt"
        else
            warn "téléchargement interrompu : il reprendra en arrière-plan (jarvis doctor pour suivre)"
        fi
    fi

    # ── 6. App de bureau reliée à ce fork ───────────────────────────────
    set_openjarvis_root "$src_dir"
    if ((install_app)); then
        step "App de bureau"
        install_desktop_app || warn "app non installée : télécharge-la sur https://github.com/open-jarvis/OpenJarvis/releases/latest"
    fi

    finish "$model" "$install_app"
}

# ── Helpers ─────────────────────────────────────────────────────────────

info() { printf '  \033[0;34m•\033[0m %s\n' "$*"; }
ok()   { printf '  \033[0;32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[1;33m!\033[0m %s\n' "$*" >&2; }
fail() { printf '\n  \033[0;31m✗ %s\033[0m\n\n' "$*" >&2; exit 1; }
step() { printf '\n\033[1m▶ %s\033[0m\n' "$*"; }

banner() {
    printf '\n\033[1m'
    echo "  ┌──────────────────────────────────────┐"
    echo "  │   J.A.R.V.I.S — installation (Linux) │"
    echo "  └──────────────────────────────────────┘"
    printf '\033[0m'
}

tty_or_null() {
    if [[ -r /dev/tty ]] && { : < /dev/tty; } 2>/dev/null; then
        echo /dev/tty
    else
        echo /dev/null
    fi
}

has_portaudio() {
    ldconfig -p 2>/dev/null | grep -q 'libportaudio\.so'
}

# L'app de bureau cherche le code source via OPENJARVIS_ROOT. On le déclare
# pour la session graphique (environment.d) et pour les shells de connexion.
set_openjarvis_root() {
    local root="$1"
    mkdir -p "$HOME/.config/environment.d"
    printf 'OPENJARVIS_ROOT=%s\n' "$root" > "$HOME/.config/environment.d/60-openjarvis.conf"
    local profile="$HOME/.profile"
    local line="export OPENJARVIS_ROOT=\"$root\"  # OpenJarvis (setup-jarvis.sh)"
    if [[ -f "$profile" ]] && grep -q '# OpenJarvis (setup-jarvis.sh)' "$profile"; then
        sed -i "s|^export OPENJARVIS_ROOT=.*# OpenJarvis (setup-jarvis.sh)\$|$line|" "$profile"
    else
        printf '\n%s\n' "$line" >> "$profile"
    fi
    export OPENJARVIS_ROOT="$root"
}

install_desktop_app() {
    if [[ "$(uname -m)" != "x86_64" ]]; then
        warn "l'app de bureau Linux n'existe qu'en x86_64 (ta machine : $(uname -m))"
        return 1
    fi
    if ! command -v apt-get >/dev/null 2>&1; then
        warn "pas d'apt-get : prends le .rpm ou l'.AppImage sur la page des versions"
        return 1
    fi
    local tmp deb
    tmp="$(mktemp -d)"
    deb="$tmp/OpenJarvis_amd64.deb"
    info "téléchargement de l'app de bureau…"
    curl -fL --retry 3 -o "$deb" \
        "https://github.com/open-jarvis/OpenJarvis/releases/latest/download/OpenJarvis_amd64.deb" \
        || { rm -rf "$tmp"; return 1; }
    sudo apt-get install -y "$deb" || { rm -rf "$tmp"; return 1; }
    rm -rf "$tmp"
    ok "app « OpenJarvis » installée (menu des applications)"
}

finish() {
    local model="$1" install_app="$2"
    printf '\n\033[1;32m'
    echo "  ┌──────────────────────────────────────┐"
    echo "  │        JARVIS est prêt, monsieur.    │"
    echo "  └──────────────────────────────────────┘"
    printf '\033[0m\n'
    echo "  Modèle : $model (100 % local, via Ollama)"
    echo
    if ((install_app)); then
        echo "  • L'app : menu des applications → « OpenJarvis »"
        echo "    (déconnecte-toi puis reconnecte-toi une fois pour qu'elle trouve ton fork)"
    fi
    echo "  • Dans un terminal :"
    echo "      jarvis                 discuter"
    echo "      jarvis gui             interface dans le navigateur"
    echo "      jarvis doctor          vérifier que tout va bien"
    echo "      jarvis connect gdrive  brancher Gmail / Agenda pour le briefing"
    echo "      jarvis digest --fresh  écouter le briefing du matin"
    echo
    echo "  Si « jarvis » est introuvable : ouvre un nouveau terminal."
    echo "  Le moteur Rust et un modèle plus gros finissent de s'installer en arrière-plan."
    echo
}

main "$@"
