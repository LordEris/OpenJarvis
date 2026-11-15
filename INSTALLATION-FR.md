# Installer JARVIS (Windows et Linux)

Une seule commande installe tout : le moteur OpenJarvis, le modèle d'IA local (Ollama), la voix française, et l'app de bureau. Tout tourne **sur ton ordinateur** : aucune clé API, aucune donnée envoyée dans le cloud.

Compte 15 à 30 minutes la première fois (selon ta connexion) et environ 10 à 15 Go d'espace disque.

## Windows 10 / 11

1. Ouvre le menu Démarrer, tape **PowerShell** et ouvre-le (pas besoin du mode administrateur).
2. Colle cette ligne et appuie sur **Entrée** :

```powershell
irm https://raw.githubusercontent.com/LordEris/OpenJarvis/main/deploy/windows/setup-jarvis.ps1 | iex
```

Si Windows bloque le script, lance d'abord `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, puis recommence.

Le script te demandera d'installer les **outils de compilation Microsoft** (« Build Tools for Visual Studio ») s'ils manquent. Réponds **O** : c'est un gros téléchargement avec une fenêtre d'autorisation Windows, mais le moteur d'OpenJarvis en a besoin.

## Linux (Zorin OS, Ubuntu, Debian…)

1. Ouvre le **Terminal**.
2. Colle cette ligne et appuie sur **Entrée** :

```bash
curl -fsSL https://raw.githubusercontent.com/LordEris/OpenJarvis/main/scripts/setup-jarvis.sh | bash
```

Ne mets pas `sudo` devant : le script demandera ton mot de passe lui-même quand il en a besoin.

## Ce que fait l'installation

| Étape | Détail |
|---|---|
| Code source | Ce fork, dans `%LOCALAPPDATA%\OpenJarvis\src` (Windows) ou `~/.openjarvis/src` (Linux) |
| Moteur | Python, uv, Ollama, et le moteur Rust (en arrière-plan sous Linux) |
| Modèle d'IA | Choisi selon ta RAM : `qwen3.5:2b`, `4b`, `9b` ou `27b` |
| Voix | Kokoro (voix française `ff_siwis`) et faster-whisper pour la dictée, en local |
| Configuration | Preset `all-in-one` : chat, code, fichiers, recherche web, mémoire, briefing du matin, tout en français |
| App de bureau | L'app OpenJarvis officielle, reliée à ce fork |

Ton ancienne configuration, s'il y en avait une, est sauvegardée à côté (`config.toml.avant-setup-…`).

## Ensuite

- **L'app** : menu Démarrer (Windows) ou menu des applications (Linux) → **OpenJarvis**. Sous Linux, déconnecte-toi puis reconnecte-toi une fois après l'installation pour qu'elle trouve ton fork.
- **Dans un terminal** (ouvre-en un nouveau après l'installation) :

| Commande | Ce qu'elle fait |
|---|---|
| `jarvis` | Discuter avec JARVIS |
| `jarvis gui` | L'interface dans ton navigateur |
| `jarvis ask --agent deep_research "…"` | Recherche approfondie avec sources |
| `jarvis memory index ~/Documents` | Indexer tes documents pour qu'il puisse les retrouver |
| `jarvis connect gdrive` | Brancher Gmail, Agenda et Tasks (pour le briefing) |
| `jarvis digest --fresh` | Écouter le briefing du matin |
| `jarvis doctor` | Vérifier que tout fonctionne |

**Sécurité** : avant de lancer une commande système, JARVIS te demande la permission dans le terminal (dans l'app, ces commandes sont refusées). Il ne peut lire et écrire des fichiers que dans Documents, Bureau et Téléchargements ; pour changer la liste, modifie `allowed_dirs` dans la section `[security]` de la configuration.

## Options

**Linux** : ajoute les options après `bash -s --`, par exemple :

```bash
curl -fsSL https://raw.githubusercontent.com/LordEris/OpenJarvis/main/scripts/setup-jarvis.sh | bash -s -- --no-app
```

| Linux | Windows (avant la commande) | Effet |
|---|---|---|
| `--no-app` | `$env:JARVIS_NO_APP='1'` | Ne pas installer l'app de bureau |
| `--keep-config` | `$env:JARVIS_KEEP_CONFIG='1'` | Garder ta configuration actuelle |
| `--analytics` | `$env:JARVIS_ANALYTICS='1'` | Partager des statistiques d'usage anonymes (désactivé sinon) |
| `--minimal` | — | Ne pas attendre le téléchargement du modèle |

Pour tester une autre branche du fork : `OPENJARVIS_BRANCH=ma-branche` (Linux) ou `$env:OPENJARVIS_BRANCH='ma-branche'` (Windows).

## Mettre à jour

Relance la même commande : elle récupère la dernière version de ce fork et garde tes données. Ta configuration est remplacée par le preset (l'ancienne est sauvegardée) ; ajoute `--keep-config` pour la garder.

## Modifier la configuration

Le fichier est `~/.openjarvis/config.toml` (sous Windows : `C:\Users\TON-NOM\.openjarvis\config.toml`). Par exemple :

```bash
jarvis config set intelligence.default_model qwen3.5:9b   # changer de modèle
jarvis config set digest.timezone America/Montreal        # changer de fuseau horaire
```

## Désinstaller

- Linux : `jarvis-uninstall`
- Windows : supprime le dossier `%LOCALAPPDATA%\OpenJarvis` et l'app OpenJarvis (Paramètres → Applications)

Ollama et ses modèles restent installés : désinstalle-les à part si tu veux libérer la place.
