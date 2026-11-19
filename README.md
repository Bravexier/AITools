# AITools

Plugins Claude Code utiles dans tous les projets, en sessions cloud comme en sessions locales.

Ce dépôt est un marketplace : `.claude-plugin/marketplace.json` liste les plugins rangés sous `plugins/`.

## Plugins

| Plugin | Rôle |
| --- | --- |
| `sonde-chargement` | Sonde provisoire. Prouve qu'un plugin installé charge son module de hooks et dit si un panneau s'affiche. À retirer une fois l'essai fait. |

## Installer un plugin

Dans un terminal :

```bash
claude plugin install <plugin> --marketplace Bravexier/AITools
```

À l'invite d'une session en terminal :

```
/plugin install <plugin> --marketplace Bravexier/AITools
```

Dans l'application de bureau : `+` à côté de la zone de saisie, puis Plugins, « Add marketplace » avec `Bravexier/AITools`, puis « Add plugin ».
