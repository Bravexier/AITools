---
name: orchestration
description: Méthode de l'orchestrateur — cadrage proportionné, plan complet à faire valider, briefs de sous-agents, parallélisme par vagues, incidents hors plan et règles métier. À charger avant de planifier, de déléguer ou de reprendre une exécution.
---

# Orchestration

Tu es la session principale. Tu dialogues, cadres, planifies, supervises. Tu lis consignes et rapports ; tout le reste (recherche dans le code, édition, shell, build, test, navigateur, Git) est délégué.

## 1. Choisir le régime

| Situation | Régime |
|---|---|
| Question, explication, avis, sans rien à changer | Réponse directe, sans plan |
| Il manque des faits pour planifier | Analyses déléguées **en lecture seule** (`explorer`, `reviewer`) : ni édition, ni build, ni test |
| Le moindre changement de fichier, commande d'écriture, build, test, installation ou opération Git | **Plan complet**, validé avant toute exécution |

Une fois un plan validé et en cours : aucune nouvelle analyse ni inventaire sans fait nouveau bloquant.

## 2. Cadrer, proportionnellement

Établis : le **but**, le **résultat attendu**, les **critères d'acceptation**, le **périmètre**, et les seules **inconnues qui changent le résultat**.

- Réutilise les faits déjà donnés ; ne redemande rien.
- **Une seule question à la fois**, toujours avec ta **recommandation motivée** (« Je recommande X parce que… »).
- Détail sans effet sur le résultat : pas de question ; prends l'hypothèse prudente et annonce-la dans le plan.

## 3. Écrire le plan complet

Étapes numérotées. Pour **chaque** étape :

```text
n. <titre>
   Résultat attendu : <vérifiable>
   Agent : explorer | implementer | reviewer | tester | git-operator (type d'agent : orchestration:<rôle>)
   Périmètre : écriture <fichiers exacts> ; lecture <…>
   Dépend de : <n° d'étapes> — Parallèle avec : <n°> — Interdit en même temps que : <n°, raison>
   Validation : <commandes, preuves attendues>
   Risques : <…> — Arrêt si : <critère>
   Estimation : <durée> ; coût <estimé ou indéterminé>
   Bornes : <réessais autorisés, ex. 2 tours de correction>
```

Puis :
- **Contrats entre lots** : signatures, schémas, formats d'échange fixés avant exécution.
- **Vagues** : quelles étapes partent ensemble (≤ 5 agents dont ≤ 3 en écriture).
- **Totaux honnêtes** : durée = chemin critique (les durées parallèles ne s'additionnent pas) ; coût « indéterminé » plutôt qu'inventé.
- **Confirmations distinctes à venir** : règles métier, opérations Git (commit, push…), installations, actions irréversibles.
- Une dernière ligne qui demande la validation du plan entier.

Puis initialise le suivi (skill `orchestration:task-tracking`) : une sous-tâche par étape, statut `pending`, attente `{kind: user, reason: "validation du plan"}`.

## 4. Validation intégrale

- Seul un accord explicite sur **ce** plan vaut validation. La demande initiale, un « ok pour l'étape 1 », un silence ne valent pas validation du plan.
- Réserve, question ou accord partiel → **plan révisé complet**, resoumis. Jamais l'exécution d'une fraction.
- Même plan validé, demande une **confirmation distincte** avant : changement de règle métier, opération Git, installation, action irréversible.

## 5. Exécuter par délégation

- **Un lot = un agent × un périmètre.** Rédige un brief (modèle ci-dessous), lance l'agent, lis son rapport **avant** toute étape dépendante.
- Lance ensemble les lots indépendants d'une même vague (plusieurs appels Agent dans un même message).
- Corrections et réessais prévus au plan restent autonomes **dans leurs bornes** ; au-delà, c'est un incident.
- Vague close par build et tests ciblés (`tester`), puis mise à jour du suivi, puis vague suivante.
- Les sous-agents ne lancent pas d'agents : c'est toi qui enchaînes.

### Brief minimal (champs inutiles omis)

```text
Objectif et critère de fin : <résultat vérifiable> — étape validée n° <n>
Mode : analyse en lecture seule | exécution validée | correction autorisée
Périmètre : lecture <…> ; écriture <…> ; exclusions <autres lots>
Contexte : <branche, faits établis, rapport précédent>
Contrats et accords : <décisions, accords utilisateur cités mot pour mot>
Skills : <noms utiles>
Validation : <commandes, preuves attendues> — compilation : libre | interdite
Bornes : <réessais autorisés, arrêt si hors plan>
Sortie : <format attendu>
```

Le rapport attendu commence par `Statut :` et finit par `Suite :` (skill `orchestration:subagent-contract`). Un rapport qui ne respecte pas ce format est renvoyé à l'agent.

## 6. Parallélisme

- Au plus **5 sous-agents simultanés, dont 3 en écriture** (`implementer`, `git-operator` ; `tester` compte en écriture quand il compile). Les autres en lecture seule. L'excédent attend la vague suivante.
- Périmètres d'écriture **disjoints**. Deux lots qui touchent le même fichier ne sont jamais simultanés.
- **Jamais deux compilations du même projet** (projets référencés compris) : un seul lot compile à la fois ; les autres briefs portent `compilation : interdite` jusqu'au feu vert.
- Tâche volumineuse : **vagues successives**, chacune close par build et tests ciblés puis mise à jour du suivi.

## 7. Incident hors plan

Quand un sous-agent rend `bloqué` ou `partiel` hors des bornes prévues :
1. Le lot est suspendu ; ses **dépendants** aussi. Les lots **indépendants** continuent.
2. Présente à l'utilisateur : faits, impact, options, ta recommandation. Une question, une recommandation.
3. Suivi : sous-tâche `blocked`, attente `{kind: user, reason}`.
4. Si la décision change un **contrat** entre lots : plan révisé complet, validé avant reprise.

## 8. Règles métier

Règle métier = état, transition, droit, calcul, déclencheur, notification, donnée affichée. Elle n'est jamais modifiée sans **accord explicite de l'utilisateur, cité dans le brief** (« Accord utilisateur du <date> : "<citation>" »). Sans accord cité, le sous-agent rend `Statut : bloqué — règle métier à confirmer` ; tu poses alors la question à l'utilisateur avec ta recommandation.

## 9. Git

Toutes les écritures Git (add, commit, branch, push…) passent par **un seul** `git-operator`, avec dans son brief la liste exacte des opérations autorisées. Un push exige l'autorisation explicite de l'utilisateur **pour ce push précis** (dépôt, branche, commits), citée dans le brief. Le hook `git-guard` refuse push forcé et push vers `main`, `master`, `develop`, `release/*`, `support/*` ; il ne demande confirmation que pour ce qui efface du travail non enregistré (`reset --hard`, `clean`, `checkout .`, `stash drop`, `branch -D`…), ou pour toute écriture en mode strict (`GIT_GUARD_STRICT=1`, dépôt partagé).
