---
name: task-tracking
description: Tenir le suivi de la tâche orchestrée dans .claude/state/sessions/<session>/task.json — modèle, progression pondérée des feuilles, attente typée, agents déclarés, révisions et cadence des mises à jour. À charger avant d'initialiser ou de mettre à jour le suivi.
---

# Suivi de tâche

L'état de chaque session vit dans `.claude/state/sessions/<session>/task.json` du projet. Il n'est jamais édité à la main : seul le script l'écrit, sous verrou, avec contrôle de révision.

```bash
sh "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/task-state.py" --session "${CLAUDE_SESSION_ID}" read
```

La commande donnée par le contexte de session porte déjà `--session` : reprends-la telle quelle. `--file` force un chemin. Sans identifiant de session connu, l'ancien `.claude/state/task.json` sert encore. `observed.jsonl` est dans le dossier de la session.

| Commande | Effet |
|---|---|
| `read` | État complet, avec `revision` et `progress` calculé |
| `update --expected-revision N --data '<json>'` | Remplace l'état complet |
| `patch --expected-revision N --data '<json>'` | Fusionne : `task` champ à champ, `waiting` remplacé, `subtasks`/`agents` fusionnés par `id`, `remove: {subtasks: [ids], agents: [ids]}` |
| `show` | Arbre, pourcentage, attente, agents (ce qu'affiche `/task-status`) |

Codes de sortie : 3 = révision périmée, 4 = état invalide (message explicite), 5 = verrou occupé.

## Modèle

```json
{
  "task": { "title": "…", "summary": "…", "lastMessage": "…", "startedAt": "auto",
            "status": "in_progress|paused|blocked|done|failed|cancelled", "remainingMinutes": 35 },
  "subtasks": [
    { "id": "1", "title": "…", "owner": "implementer", "parentId": null, "dependsOn": [],
      "status": "pending|in_progress|done|blocked|failed|cancelled", "progress": 0, "weight": 1,
      "phase": "vague 1", "estimatedMinutes": 20, "statusSince": "auto" }
  ],
  "waiting": null,
  "agents": [ { "id": "a1", "name": "implementer", "status": "pending|running|waiting|idle|completed|failed|cancelled",
                "taskId": "1", "since": "auto" } ]
}
```

- `remainingMinutes` : `null` si non estimé — jamais inventé.
- `waiting` : `null` ou `{ "kind": "user|agents|external", "reason": "…" }` (`since` auto). **Lève-la** (`null`) dès qu'elle cesse.
- `statusSince`, `since`, `startedAt` sont posés par le script quand le statut change.

## Règles de progression

- **Progression globale = moyenne pondérée par `weight` des seules feuilles** (sous-tâches sans enfant) ; feuilles `cancelled` exclues.
- **Piège** : mettre à jour un parent ne change pas le pourcentage. Fais progresser ses **feuilles**.
- `pending` = 0, `done` = 100, aucun autre statut à 100 (le script refuse).
- Progression fondée sur des **jalons prouvés** (fichier écrit, test vert, revue rendue), jamais sur le temps écoulé.
- `task.status = done` est refusé tant qu'une feuille non annulée n'est pas `done`, qu'un agent est `pending|running|waiting` ou qu'une attente est ouverte.

## Agents : déclaré ≠ observé

`agents` contient ce que **tu déclares** (tu as lancé tel agent pour telle sous-tâche). Les hooks `SubagentStart`/`SubagentStop` notent à part, dans `observed.jsonl`, ce que **l'hôte observe**, sans association à une sous-tâche. Ne déduis jamais l'une de l'autre.

## Concurrence

1. `read` → note `revision`.
2. `patch` ou `update` avec `--expected-revision` = cette révision.
3. Code 3 (conflit) → **relis**, réconcilie avec l'état actuel, renvoie. Jamais d'écrasement.

## Cadence

Mises à jour aux **seuls jalons** : plan soumis (feuilles `pending`, attente `user`), démarrage d'une vague (sous-tâches `in_progress`, agents `running`, attente `agents`), rapport reçu, fin de vague, validation, réestimation, blocage, fin. Aucun sondage, aucun message artificiel.

Si l'outil `mcp__suivi-demande__suivre` (plugin suivi-demande) est présent, reporte-y les mêmes jalons : `task.json` reste la source de vérité de l'orchestration.

## Exemple

```bash
sh "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/task-state.py" --session "${CLAUDE_SESSION_ID}" patch --expected-revision 4 --data '{
  "subtasks": [{"id": "1.1", "status": "done"}, {"id": "1.2", "status": "in_progress", "progress": 30}],
  "agents": [{"id": "a2", "status": "completed"}],
  "task": {"lastMessage": "Vague 1 : lot A livré, tests en cours", "remainingMinutes": 25}
}'
```
