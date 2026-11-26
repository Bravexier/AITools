# orchestration

Méthode générique de travail à plusieurs agents pour Claude Code ([issue n°5](https://github.com/Bravexier/AITools/issues/5)) : un **orchestrateur** qui dialogue, cadre, fait valider un plan complet et supervise ; des **sous-agents** spécialisés qui exécutent des lots bornés ; des **écritures Git verrouillées** ; un **suivi de tâche** pondéré et versionné.

Installation : voir le [README du dépôt](../../README.md), avec `orchestration` pour nom de plugin. Une fois installé (portée utilisateur), il s'applique à **tous** les projets de la machine ou de l'environnement cloud.

## Le workflow

```text
Message de l'utilisateur
 ├─ conversation pure ─────────────→ réponse directe, sans plan
 ├─ il manque des faits ───────────→ explorer / reviewer en lecture seule
 └─ la moindre exécution ──────────→ plan complet → validation du plan ENTIER
                                       │  (réserve ou accord partiel → plan révisé complet)
                                       ▼
                            vague 1 : ≤ 5 agents, ≤ 3 en écriture, périmètres disjoints
                                       │  rapports lus → build + tests ciblés → suivi à jour
                                       ▼
                            vague 2 … → git-operator (confirmation distincte, push autorisé)
```

- **Plan** : pour chaque étape, résultat attendu, agent, périmètre (fichiers), dépendances, parallélisme et interdits de concurrence, validation, risques, critères d'arrêt, estimation. Totaux honnêtes (chemin critique, coût « indéterminé » plutôt qu'inventé), contrats entre lots fixés.
- **Confirmations distinctes**, même plan validé : règle métier, opération Git, installation, action irréversible.
- **Incident hors plan** : le lot et ses dépendants sont suspendus, les lots indépendants continuent ; l'utilisateur tranche ; un contrat changé impose un plan révisé validé.
- **Règle métier** (état, transition, droit, calcul, déclencheur, notification, donnée affichée) : jamais modifiée sans accord explicite cité dans le brief, sinon `bloqué — règle métier à confirmer`.

## Les rôles

| Agent | Outils | Rôle |
|---|---|---|
| `orchestration:explorer` | Read, Grep, Glob | Analyse en lecture seule (modèle Sonnet) |
| `orchestration:implementer` | Read, Grep, Glob, Edit, Write, Bash | Lot d'exécution validé, dans son seul périmètre d'écriture |
| `orchestration:reviewer` | Read, Grep, Glob, Bash | Revue en lecture seule (Bash pour `git diff`/`log`) (modèle Sonnet) |
| `orchestration:tester` | Read, Grep, Glob, Bash | Build et tests ciblés, résultats réels, aucune correction |
| `orchestration:git-operator` | Read, Bash | Seul agent qui écrit dans Git, opérations nommées par son brief |

Aucun agent n'a l'outil de délégation (`Agent`) : seul l'orchestrateur enchaîne. Chaque agent précharge le skill `subagent-contract` et rend un rapport qui commence par `Statut : terminé | partiel | bloqué — …` et finit par `Suite : …`.

## Ce que contient le plugin

| Élément | Fichier | Rôle |
|---|---|---|
| Règles permanentes | `context/orchestrateur.md`, ajouté au contexte par le hook `SessionStart` | L'équivalent d'un `CLAUDE.md` commun à tous les projets, court, qui renvoie aux skills |
| Skill `orchestration` | `skills/orchestration/SKILL.md` | Régimes, cadrage, plan, validation, brief, parallélisme, incidents |
| Skill `subagent-contract` | `skills/subagent-contract/SKILL.md` | Règles communes et format du rapport |
| Skill `task-tracking` | `skills/task-tracking/SKILL.md` | Modèle et usage du suivi |
| Agents | `agents/*.md` | Les cinq rôles ci-dessus |
| Garde-fou Git | `hooks/git-guard.py`, hook `PreToolUse` (Bash) | `ask` / `deny`, voir plus bas |
| Suivi | `scripts/task-state.py` | `read`, `patch`, `update`, `show`, `observe` |
| Commande | `commands/task-status.md` | `/task-status` (ou `/orchestration:task-status`) |
| Observation | hooks `SubagentStart` / `SubagentStop` | Événements de l'hôte notés à part, dans `.claude/state/observed.jsonl` |

Les scripts sont en Python 3.8+ (bibliothèque standard seulement), lancés par `scripts/py.sh` qui essaie `python3`, `python` puis `py` : sous Windows, l'alias `python3` du Microsoft Store est écarté s'il ne fonctionne pas.

## Garde-fou Git

Le hook analyse toute la commande : enchaînements `;`, `&&`, `||`, `|`, `&`, retours à la ligne, `$(…)`, `bash -c "…"`, variables et `sudo` en tête, options globales `-C`, `-c`, `--git-dir`…

| Décision | Commandes |
|---|---|
| `deny` | push forcé (`--force`, `-f`, `--force-with-lease`, `+refspec`, `--mirror`), push groupé (`--all`), push vers `main`, `master`, `develop`, `release/*`, `support/*` — y compris `HEAD:main`, `--delete main`, et un push implicite depuis une de ces branches |
| `ask` | commit, push, reset, rebase, merge, pull, cherry-pick, revert, checkout, switch, restore, clean, add, rm, mv, stash (hors `list`/`show`), création ou suppression de tag ou de branche, `config` et `remote` en écriture |
| aucune | tout le reste (`status`, `log`, `diff`, `fetch`, `show`…), entrée invalide, erreur interne |

Le hook ne renvoie jamais `allow` : il ne contourne pas les permissions. Sans Python, il ne décide rien (jamais de blocage involontaire). Les alias Git (`git co`) ne sont pas résolus.

## Suivi de tâche

État dans `.claude/state/task.json` du projet (à ignorer dans Git si besoin) : tâche maître, sous-tâches pondérées avec parent et dépendances, attente typée (`user|agents|external`), agents déclarés. Progression globale = moyenne pondérée **des seules feuilles** ; `pending` = 0, `done` = 100 ; la tâche ne passe `done` que si tout est fini, sans agent actif ni attente. Toute écriture exige `--expected-revision` : une révision périmée est rejetée (code 3), à relire et réconcilier. Détails : [`skills/task-tracking/SKILL.md`](skills/task-tracking/SKILL.md).

```text
/task-status

# Livrer la fonctionnalité
Statut : in_progress · Progression : 60.0 % (feuilles pondérées) · Reste : 25 min
## Sous-tâches
├─ [ ] 1 Lot A — pending · 75.0 % (feuilles) · implementer
│  ├─ [x] 1.1 Code — done · 100 % · poids 3
│  └─ [~] 1.2 Tests — in_progress · 0 % · poids 1 · après 1.1
└─ [ ] 2 Revue — pending · 0 % · poids 1 · après 1
## Attente
agents — tests du lot A (depuis …)
## Agents déclarés (par l'orchestrateur)
- implementer (a1) : running depuis … → 1.2
```

Avec le plugin [`suivi-demande`](../suivi-demande/README.md), l'orchestrateur reporte les mêmes jalons dans la page publiée ; `task.json` reste la source de vérité.

## Désactiver

`ORCHESTRATION_DISABLED=1` dans l'environnement (ou dans `env` d'un `.claude/settings.json` de projet) retire les règles de l'orchestrateur du contexte. Le garde-fou Git reste actif ; pour tout couper, désactiver le plugin : `claude plugin disable orchestration@aitools`.

## Limites

- Plan, validation et parallélisme reposent sur les consignes données au modèle : seuls le garde-fou Git et les contrôles du script de suivi sont imposés par du code.
- Une session cloud sans personne pour répondre s'arrête aux `ask` du garde-fou et à la validation du plan : c'est voulu.
- Les sous-agents de plugin ignorent `hooks`, `mcpServers` et `permissionMode` de leur frontmatter : les garde-fous sont au niveau du plugin.

## Contrôler

```bash
claude plugin validate plugins/orchestration
```

```bash
python3 -m unittest discover -s plugins/orchestration/tests -v
```
