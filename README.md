# AITools

Plugins Claude Code utiles dans tous les projets, en sessions cloud comme en sessions locales.

Ce dépôt est un marketplace : `.claude-plugin/marketplace.json` liste les plugins rangés sous `plugins/`.

## Plugins

| Plugin | Rôle |
| --- | --- |
| [`suivi-demande`](plugins/suivi-demande/README.md) | Suit chaque demande : but, étapes, sous-tâches, temps, agents. Panneau en local, page publiée en cloud. |

## Installer un plugin

Une fois par surface, puis le plugin se charge dans chaque projet.

### Sessions locales (une fois par machine)

Dans un terminal :

```bash
claude plugin install <plugin> --marketplace Bravexier/AITools
```

Répondre `y` pour ajouter le marketplace, puis choisir la portée utilisateur. Le plugin se charge ensuite dans chaque session neuve, terminal comme application de bureau.

Mettre à jour :

```bash
claude plugin marketplace update aitools
```

```bash
claude plugin update <plugin>@aitools
```

### Sessions cloud (une fois par environnement)

Dans les réglages de l'environnement cloud, le script de démarrage installe le plugin à chaque conteneur neuf :

```bash
#!/bin/bash
# Plugins Claude Code du marketplace Bravexier/AITools, pour toutes les sessions.
if command -v claude >/dev/null 2>&1; then
  claude plugin install <plugin> --marketplace Bravexier/AITools --scope user || true
fi
```

Chaque session neuve prend alors la dernière version publiée sur `main`.

## Ajouter un plugin

Un dossier sous `plugins/<nom>/` (`.claude-plugin/plugin.json`, `hooks/`, tests), une entrée de plus dans `.claude-plugin/marketplace.json`, puis :

```bash
claude plugin validate plugins/<nom>
```

```bash
claude plugin test plugins/<nom>
```
