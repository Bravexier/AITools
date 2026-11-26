---
description: Affiche le suivi de la tâche orchestrée — arbre des sous-tâches, pourcentage pondéré, attente et agents.
allowed-tools: Bash(sh:*)
---

Suivi actuel (`.claude/state/task.json`) :

!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/task-state.py" show`

Recopie ce suivi tel quel dans un bloc de texte, sans le reformuler. Ajoute ensuite au plus une ligne : ce qui bloque ou ce qui est attendu de l'utilisateur, s'il y a une attente ou une sous-tâche `blocked`. Ne lance aucune autre action.
