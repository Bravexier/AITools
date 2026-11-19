# suivi-demande

Suit chaque demande faite à Claude Code et l'affiche à côté de la conversation : but, étapes, sous-tâches (faites, en cours, programmées, en attente) avec leurs durées estimées, temps de travail mesuré, reste estimé, et agents (en marche, en file, en attente d'un agent, en attente de vous, terminés).

Installation : voir le [README du dépôt](../../README.md), avec `suivi-demande` pour nom de plugin.

## Où le suivi s'affiche

- **Panneau du mod**, quand une surface le dessine (application de bureau, terminal). Il suffit : aucune page n'est publiée.
- **Page publiée** (`page/suivi-demande.html`, Artifact avec les capacités `db` et `user`), quand aucun panneau ne se dessine : c'est le cas des sessions cloud. La session la publie à la première demande ; le mod écrit l'état dans le document `suivi/etat` de sa base, la page le lit en direct.

## Ce qui dépend de la session

| | Session locale | Session cloud |
| --- | --- | --- |
| Étapes | Analyse, Développement, Vérification | Les mêmes, puis Publication, CI, Fusion |
| Branche, PR, CI | Jamais | Suivies |
| Clôture automatique | Jamais | À la fusion de la PR |
| Clôture à la demande | `/suivi clore` | `/suivi clore` |

Une nouvelle demande remplace le suivi en cours. À la clôture, le panneau se ferme ; s'il y avait une page, la session la supprime (l'application demande confirmation).

## Réglages d'un dépôt

Un dépôt dont les demandes fusionnées s'installent sur un site ajoute `.claude/suivi-demande.json` :

```json
{
  "installation": {
    "nom": "Site de dev",
    "url": "https://dev.example.com/",
    "motif": ">V(\\d+\\.\\d+\\.\\d+)<",
    "consigne": "La version cible est celle que le bot donne à la PR."
  }
}
```

- `url` : la page qui porte la version servie (`https://` obligatoire).
- `motif` : expression régulière dont le premier groupe capture cette version.
- `nom` et `consigne` : facultatifs ; la consigne dit à la session comment trouver la version cible.

En session cloud, le suivi gagne alors l'étape « Installation » : après la fusion, la session donne `version_cible`, et le suivi ne se clôt que lorsque le site sert cette version ou une plus récente. Sans ce fichier, rien de tout cela n'existe.

## Ce que fait le mod

- **Outil `mcp__suivi-demande__suivre`** : la session principale y déclare le but, les sous-tâches, la phase et ce qu'elle attend de vous. Une consigne ajoutée au prompt le lui rappelle ; les agents n'ont ni l'outil ni la consigne.
- **Relevé sans la session** : agents et leur état, temps de travail, tours de travail, ouverture et fusion d'une PR.
- **Commande `/suivi`** : ouvre le panneau ; `/suivi clore` clôt le suivi.
- **Reprise** : l'état est gardé par session (`$.store`) et survit à un redémarrage. Redéclarer la même demande ne remet pas le temps à zéro.

## Limites

- Entre deux tours, une session cloud dort : la page n'est écrite qu'au début et à la fin de chaque tour, et pendant. Ses durées continuent de défiler, mais rien d'autre n'y bouge tant que la session n'est pas réveillée.
- L'état de la CI est déclaré par la session à chaque événement de la PR, pas lu sur GitHub.
- Une PR fusionnée hors de la session (par vous sur GitHub) n'est constatée que lorsque la session l'apprend et le déclare.
- La session cloud est reconnue par la variable `CLAUDE_CODE_REMOTE`.

## Contrôler

```bash
claude plugin validate plugins/suivi-demande
```

```bash
claude plugin test plugins/suivi-demande
```
