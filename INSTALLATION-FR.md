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

## Mode mains libres (« Jarvis, … »)

Lance le raccourci **JARVIS (vocal)**, sur le bureau (Windows) ou dans le menu des applications (Linux), ou tape `jarvis chat --wake`. JARVIS écoute en continu et réagit dès qu'une phrase commence ou finit par son nom :

- « Jarvis, ouvre Spotify » : il le fait directement ;
- « Jarvis ? » : il répond « Oui ? » et écoute ta demande ;
- « Jarvis, au revoir » (ou Ctrl+C) : il s'arrête.

Tout reste sur ton PC : chaque phrase est transcrite en local, et celles qui ne s'adressent pas à JARVIS sont ignorées. Pour changer de nom, par exemple « Vendredi » : `jarvis config set speech.wake_word vendredi`.

## Le cerveau : JARVIS apprend

Après chaque conversation, JARVIS note en arrière-plan ce qu'il a appris : qui tu es, tes proches, tes projets, ce qui t'intéresse. Chaque chose devient une page de `Documents/Cerveau`, reliée aux autres, et il s'en sert pour te répondre les fois suivantes.

- « Jarvis, retiens que l'anniversaire d'Alice est le 3 mars » : il l'enregistre tout de suite.
- « Qu'est-ce que tu sais sur mon projet de voyage ? » : il cherche dans son cerveau.
- `jarvis cerveau apprendre document.pdf` : il lit un document (`.pdf`, `.txt`, `.md`) et en retient l'essentiel.
- `jarvis cerveau chercher Alice` ou `jarvis cerveau etat` : tu vois ce qu'il sait.

### La galaxie

Ouvre-la avec le raccourci **JARVIS – Galaxie**, en disant « Jarvis, montre-moi ta galaxie », ou avec `jarvis cerveau galaxie`. Chaque connaissance est une étoile reliée aux autres. Toi, tu es au centre, en doré. Plus une étoile a de liens, plus elle brille. Fais glisser pour tourner, utilise la molette pour zoomer, et clique sur une étoile pour lire ce qu'il sait. La recherche en haut retrouve une étoile par son nom.

### Garder la main

- Les pages sont de simples fichiers Markdown : ouvre le dossier dans [Obsidian](https://obsidian.md) (« Ouvrir un dossier comme coffre ») pour tout lire, corriger ou supprimer.
- JARVIS ne réécrit que ses sections « Ce que je sais » et « Liens » : ce que tu écris toi-même dans une page est conservé.
- Le dossier `journal/` garde la trace de ce qu'il a appris chaque jour.
- Pour qu'il arrête d'apprendre tout seul : `jarvis config set brain.learn_from_conversations false`.

La qualité de ce qu'il retient dépend du modèle : un petit modèle (2b ou 4b) apprend moins bien et peut se tromper, alors relis son cerveau de temps en temps. Les conversations Telegram ne l'alimentent pas.

## Piloter le PC

Demande simplement, à l'écrit ou à la voix :

- « ouvre Spotify », « ouvre YouTube », « ouvre mon dossier Documents » ;
- « mets le son à 30 % », « monte le son », « coupe le son » ;
- « mets en pause », « musique suivante ».

Par sécurité, JARVIS ne lance que les applications de ton menu Démarrer (Windows) ou de ton menu d'applications (Linux). Il n'ouvre que des liens `http`/`https`, et seulement des documents de tes dossiers autorisés, jamais des programmes ou des scripts.

Sous Linux, `sudo apt install playerctl` rend le contrôle de la musique plus fiable.

## Parler à JARVIS depuis ton téléphone (Telegram)

1. Installe Telegram sur ton téléphone et crée un bot avec **@BotFather** (`/newbot`). Il te donne un jeton.
2. Ferme l'app OpenJarvis, puis lance sur ton PC :

```bash
jarvis channel setup-telegram
```

3. Colle le jeton, puis envoie un message à ton bot depuis le téléphone : la commande reconnaît ton compte et n'autorise que lui.

Le bot répond tant que JARVIS tourne sur le PC : l'app est ouverte, ou `jarvis start` a été lancé (`jarvis stop` pour l'arrêter). Depuis le téléphone, il peut ouvrir des applis, régler le son et la musique, ou chercher sur le web. Les commandes système restent bloquées à distance.

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
