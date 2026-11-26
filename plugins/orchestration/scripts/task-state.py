#!/usr/bin/env python3
"""Suivi de tâche de l'orchestrateur : un fichier JSON versionné et verrouillé.

Fichier : --file, sinon $TASK_STATE_FILE, sinon
$CLAUDE_PROJECT_DIR/.claude/state/task.json, sinon ./.claude/state/task.json.

Commandes :
  read                              état complet, avec `revision` et `progress`
  update --expected-revision N      remplace l'état complet (JSON sur stdin ou --data)
  patch  --expected-revision N      fusionne : `task` champ à champ, `waiting`
                                    remplacé, `subtasks` et `agents` fusionnés par
                                    `id`, `remove: {subtasks: [...], agents: [...]}`
  show                              arbre, progression pondérée, attente, agents
  observe                           (hook SubagentStart/SubagentStop) note un
                                    événement observé de l'hôte, sans l'associer

Codes de sortie : 0 succès, 2 usage, 3 conflit de révision, 4 état invalide,
5 verrou indisponible.

Règles :
- progression globale = moyenne pondérée par `weight` des seules feuilles
  (sous-tâches sans enfant), feuilles `cancelled` exclues ; un parent ne compte
  jamais ;
- `pending` vaut 0, `done` vaut 100, aucun autre statut à 100 ;
- la tâche maître ne passe `done` que si toutes les feuilles non annulées sont
  `done`, qu'aucun agent n'est actif et qu'aucune attente n'est ouverte ;
- toute écriture exige la révision lue : une révision périmée est rejetée.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

STATUTS_TACHE = {"in_progress", "paused", "blocked", "done", "failed", "cancelled"}
STATUTS_SOUS_TACHE = {"pending", "in_progress", "done", "blocked", "failed", "cancelled"}
STATUTS_AGENT = {"pending", "running", "waiting", "idle", "completed", "failed", "cancelled"}
AGENTS_ACTIFS = {"pending", "running", "waiting"}
GENRES_ATTENTE = {"user", "agents", "external"}

CONFLIT, INVALIDE, VERROU = 3, 4, 5


class Erreur(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def maintenant() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def chemin_par_defaut() -> str:
    if os.environ.get("TASK_STATE_FILE"):
        return os.environ["TASK_STATE_FILE"]
    racine = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    return os.path.join(racine, ".claude", "state", "task.json")


def etat_vide() -> Dict[str, Any]:
    return {"revision": 0, "task": None, "subtasks": [], "waiting": None, "agents": [], "updatedAt": None}


# --- Fichier et verrou --------------------------------------------------------

class Verrou:
    """Verrou par fichier exclusif ; un verrou de plus de 30 s est réputé abandonné."""

    def __init__(self, chemin: str, delai: float = 5.0):
        self.chemin = chemin + ".lock"
        self.delai = delai

    def __enter__(self):
        os.makedirs(os.path.dirname(self.chemin) or ".", exist_ok=True)
        fin = time.monotonic() + self.delai
        while True:
            try:
                fd = os.open(self.chemin, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{os.getpid()} {maintenant()}".encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.chemin) > 30:
                        os.remove(self.chemin)
                        continue
                except OSError:
                    pass
                if time.monotonic() > fin:
                    raise Erreur(VERROU, f"verrou occupé : {self.chemin}")
                time.sleep(0.05)

    def __exit__(self, *exc):
        try:
            os.remove(self.chemin)
        except OSError:
            pass


def lire(chemin: str) -> Dict[str, Any]:
    try:
        with open(chemin, encoding="utf-8") as f:
            etat = json.load(f)
    except FileNotFoundError:
        return etat_vide()
    except (OSError, ValueError) as e:
        raise Erreur(INVALIDE, f"fichier d'état illisible ({chemin}) : {e}")
    base = etat_vide()
    base.update(etat if isinstance(etat, dict) else {})
    return base


def ecrire(chemin: str, etat: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(chemin) or ".", exist_ok=True)
    temporaire = f"{chemin}.{os.getpid()}.tmp"
    with open(temporaire, "w", encoding="utf-8") as f:
        json.dump(etat, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(temporaire, chemin)


# --- Calculs -------------------------------------------------------------------

def feuilles(sous_taches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    parents = {s.get("parentId") for s in sous_taches if s.get("parentId")}
    return [s for s in sous_taches if s.get("id") not in parents]


def progression(sous_taches: List[Dict[str, Any]]) -> float:
    comptees = [s for s in feuilles(sous_taches) if s.get("status") != "cancelled"]
    total = sum(float(s.get("weight", 1)) for s in comptees)
    if total <= 0:
        return 0.0
    somme = sum(float(s.get("weight", 1)) * float(s.get("progress", 0)) for s in comptees)
    return round(somme / total, 1)


# --- Validation et normalisation ----------------------------------------------

def normaliser(nouveau: Dict[str, Any], ancien: Dict[str, Any]) -> Dict[str, Any]:
    """Vérifie l'état et complète les champs dérivés (statusSince, since...)."""
    etat = {k: nouveau.get(k, v) for k, v in etat_vide().items()}
    horodatage = maintenant()

    tache = etat["task"]
    if tache is not None:
        if not isinstance(tache, dict) or not isinstance(tache.get("title"), str) or not tache["title"].strip():
            raise Erreur(INVALIDE, "task.title est obligatoire")
        if tache.get("status", "in_progress") not in STATUTS_TACHE:
            raise Erreur(INVALIDE, f"task.status invalide : {tache.get('status')}")
        tache.setdefault("status", "in_progress")
        tache.setdefault("startedAt", (ancien.get("task") or {}).get("startedAt") or horodatage)
        tache.setdefault("summary", "")
        tache.setdefault("lastMessage", "")
        tache.setdefault("remainingMinutes", None)
        reste = tache["remainingMinutes"]
        if reste is not None and (not isinstance(reste, (int, float)) or isinstance(reste, bool) or reste < 0):
            raise Erreur(INVALIDE, "task.remainingMinutes : nombre positif ou null")

    sous_taches = etat["subtasks"]
    if not isinstance(sous_taches, list):
        raise Erreur(INVALIDE, "subtasks doit être une liste")
    anciennes = {s.get("id"): s for s in ancien.get("subtasks") or [] if isinstance(s, dict)}
    ids = set()
    for s in sous_taches:
        if not isinstance(s, dict) or not isinstance(s.get("id"), str) or not s["id"]:
            raise Erreur(INVALIDE, "chaque sous-tâche exige un id texte")
        if s["id"] in ids:
            raise Erreur(INVALIDE, f"id de sous-tâche en double : {s['id']}")
        ids.add(s["id"])
    for s in sous_taches:
        sid = s["id"]
        if not isinstance(s.get("title"), str) or not s["title"].strip():
            raise Erreur(INVALIDE, f"{sid} : title obligatoire")
        s.setdefault("status", "pending")
        if s["status"] not in STATUTS_SOUS_TACHE:
            raise Erreur(INVALIDE, f"{sid} : status invalide ({s['status']})")
        s.setdefault("owner", None)
        s.setdefault("parentId", None)
        s.setdefault("dependsOn", [])
        s.setdefault("weight", 1)
        if s["parentId"] is not None and s["parentId"] not in ids:
            raise Erreur(INVALIDE, f"{sid} : parentId inconnu ({s['parentId']})")
        if not isinstance(s["dependsOn"], list) or any(d not in ids or d == sid for d in s["dependsOn"]):
            raise Erreur(INVALIDE, f"{sid} : dependsOn doit lister des ids existants")
        poids = s["weight"]
        if not isinstance(poids, (int, float)) or isinstance(poids, bool) or poids <= 0:
            raise Erreur(INVALIDE, f"{sid} : weight doit être un nombre > 0")
        precedente = anciennes.get(sid)
        if precedente and precedente.get("status") != s["status"] and s.get("progress") == precedente.get("progress"):
            # Progression héritée d'un autre statut par la fusion : recalculée.
            s.pop("progress", None)
        if s["status"] == "pending":
            s.setdefault("progress", 0)
            if s["progress"] != 0:
                raise Erreur(INVALIDE, f"{sid} : une sous-tâche pending vaut 0 %")
        elif s["status"] == "done":
            s.setdefault("progress", 100)
            if s["progress"] != 100:
                raise Erreur(INVALIDE, f"{sid} : une sous-tâche done vaut 100 %")
        else:
            s.setdefault("progress", 0)
            p = s["progress"]
            if not isinstance(p, (int, float)) or isinstance(p, bool) or not 0 <= p < 100:
                raise Erreur(INVALIDE, f"{sid} : progress entre 0 et 99 hors done ({p})")
        precedente = anciennes.get(sid)
        if "statusSince" not in s or (precedente and precedente.get("status") != s["status"] and s.get("statusSince") == precedente.get("statusSince")):
            s["statusSince"] = horodatage if not precedente or precedente.get("status") != s["status"] else precedente.get("statusSince", horodatage)
    parent = {s["id"]: s["parentId"] for s in sous_taches}
    for sid in parent:
        vus, courant = set(), sid
        while courant is not None:
            if courant in vus:
                raise Erreur(INVALIDE, f"cycle de parents autour de {sid}")
            vus.add(courant)
            courant = parent.get(courant)

    attente = etat["waiting"]
    if attente is not None:
        if not isinstance(attente, dict) or attente.get("kind") not in GENRES_ATTENTE or not attente.get("reason"):
            raise Erreur(INVALIDE, "waiting : null ou {kind: user|agents|external, reason, since}")
        precedente = ancien.get("waiting") or {}
        attente.setdefault("since", precedente.get("since") if precedente.get("kind") == attente["kind"] else horodatage)

    agents = etat["agents"]
    if not isinstance(agents, list):
        raise Erreur(INVALIDE, "agents doit être une liste")
    anciens = {a.get("id"): a for a in ancien.get("agents") or [] if isinstance(a, dict)}
    vus = set()
    for a in agents:
        if not isinstance(a, dict) or not isinstance(a.get("id"), str) or not a["id"]:
            raise Erreur(INVALIDE, "chaque agent exige un id texte")
        if a["id"] in vus:
            raise Erreur(INVALIDE, f"id d'agent en double : {a['id']}")
        vus.add(a["id"])
        a.setdefault("name", a["id"])
        a.setdefault("status", "pending")
        a.setdefault("taskId", None)
        if a["status"] not in STATUTS_AGENT:
            raise Erreur(INVALIDE, f"agent {a['id']} : status invalide ({a['status']})")
        if a["taskId"] is not None and a["taskId"] not in ids:
            raise Erreur(INVALIDE, f"agent {a['id']} : taskId inconnu ({a['taskId']})")
        precedent = anciens.get(a["id"])
        if "since" not in a or (precedent and precedent.get("status") != a["status"] and a.get("since") == precedent.get("since")):
            a["since"] = horodatage if not precedent or precedent.get("status") != a["status"] else precedent.get("since", horodatage)

    if tache is not None and tache["status"] == "done":
        restantes = [s["id"] for s in feuilles(sous_taches) if s["status"] not in {"done", "cancelled"}]
        actifs = [a["id"] for a in agents if a["status"] in AGENTS_ACTIFS]
        if restantes or actifs or attente is not None:
            raise Erreur(INVALIDE, "task done refusé : "
                         + "; ".join(filter(None, [
                             f"feuilles non terminées {restantes}" if restantes else "",
                             f"agents actifs {actifs}" if actifs else "",
                             "attente ouverte" if attente is not None else "",
                         ])))
    if tache is not None and tache["status"] == "done":
        tache["remainingMinutes"] = 0
    return etat


def fusionner_liste(actuelle: List[Dict[str, Any]], ajouts: Any, retraits: Any, nom: str) -> List[Dict[str, Any]]:
    if ajouts is None:
        ajouts = []
    if not isinstance(ajouts, list) or not all(isinstance(x, dict) and isinstance(x.get("id"), str) for x in ajouts):
        raise Erreur(INVALIDE, f"patch.{nom} : liste d'objets avec id")
    rendu = [copy.deepcopy(x) for x in actuelle if x.get("id") not in set(retraits or [])]
    index = {x["id"]: x for x in rendu}
    for ajout in ajouts:
        if ajout["id"] in index:
            index[ajout["id"]].update(ajout)
        else:
            rendu.append(copy.deepcopy(ajout))
            index[ajout["id"]] = rendu[-1]
    return rendu


def appliquer_patch(ancien: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    inconnus = set(patch) - {"task", "subtasks", "waiting", "agents", "remove"}
    if inconnus:
        raise Erreur(INVALIDE, f"patch : champs inconnus {sorted(inconnus)}")
    nouveau = copy.deepcopy({k: ancien[k] for k in ("task", "subtasks", "waiting", "agents")})
    if "task" in patch:
        if patch["task"] is None:
            nouveau["task"] = None
        elif isinstance(patch["task"], dict):
            nouveau["task"] = {**(nouveau["task"] or {}), **patch["task"]}
        else:
            raise Erreur(INVALIDE, "patch.task : objet ou null")
    if "waiting" in patch:
        nouveau["waiting"] = patch["waiting"]
    retraits = patch.get("remove") or {}
    if not isinstance(retraits, dict):
        raise Erreur(INVALIDE, "patch.remove : {subtasks: [ids], agents: [ids]}")
    nouveau["subtasks"] = fusionner_liste(nouveau["subtasks"], patch.get("subtasks"), retraits.get("subtasks"), "subtasks")
    nouveau["agents"] = fusionner_liste(nouveau["agents"], patch.get("agents"), retraits.get("agents"), "agents")
    return nouveau


def ecrire_avec_revision(chemin: str, attendue: int, construire) -> Dict[str, Any]:
    with Verrou(chemin):
        ancien = lire(chemin)
        if ancien["revision"] != attendue:
            raise Erreur(CONFLIT, f"révision périmée : attendue {attendue}, actuelle {ancien['revision']} — relire (read) et réconcilier")
        etat = normaliser(construire(ancien), ancien)
        etat["revision"] = ancien["revision"] + 1
        etat["updatedAt"] = maintenant()
        ecrire(chemin, etat)
        return etat


def avec_progression(etat: Dict[str, Any]) -> Dict[str, Any]:
    return {**etat, "progress": progression(etat.get("subtasks") or [])}


# --- Affichage -------------------------------------------------------------------

SIGNES = {"pending": "[ ]", "in_progress": "[~]", "done": "[x]", "blocked": "[!]", "failed": "[X]", "cancelled": "[-]"}


def fichier_observe(chemin: str) -> str:
    return os.path.join(os.path.dirname(chemin), "observed.jsonl")


def afficher(chemin: str) -> str:
    etat = lire(chemin)
    lignes: List[str] = []
    tache = etat.get("task")
    if tache is None and not etat["subtasks"]:
        return f"Aucune tâche suivie ({chemin})."
    tache = tache or {"title": "(sans titre)", "status": "?"}
    reste = tache.get("remainingMinutes")
    lignes.append(f"# {tache['title']}")
    lignes.append(f"Statut : {tache.get('status')} · Progression : {progression(etat['subtasks'])} % (feuilles pondérées)"
                  f" · Reste : {'non estimé' if reste is None else f'{reste} min'}")
    if tache.get("summary"):
        lignes.append(f"Résumé : {tache['summary']}")
    if tache.get("lastMessage"):
        lignes.append(f"Dernier message : {tache['lastMessage']}")
    lignes.append(f"Début : {tache.get('startedAt', '?')} · Révision {etat['revision']} · Mise à jour {etat.get('updatedAt')}")

    lignes.append("")
    lignes.append("## Sous-tâches")
    enfants: Dict[Optional[str], List[Dict[str, Any]]] = {}
    ids = {s["id"] for s in etat["subtasks"]}
    for s in etat["subtasks"]:
        enfants.setdefault(s.get("parentId") if s.get("parentId") in ids else None, []).append(s)
    feuilles_ids = {s["id"] for s in feuilles(etat["subtasks"])}

    def branche(parent: Optional[str], prefixe: str) -> None:
        liste = enfants.get(parent, [])
        for k, s in enumerate(liste):
            dernier = k == len(liste) - 1
            details = [s["status"]]
            if s["id"] in feuilles_ids:
                details.append(f"{s.get('progress', 0)} %")
                details.append(f"poids {s.get('weight', 1)}")
            else:
                details.append(f"{progression([x for x in etat['subtasks'] if descend(x, s['id'])])} % (feuilles)")
            if s.get("owner"):
                details.append(s["owner"])
            if s.get("phase"):
                details.append(f"phase {s['phase']}")
            if s.get("estimatedMinutes") is not None:
                details.append(f"≈ {s['estimatedMinutes']} min")
            if s.get("dependsOn"):
                details.append("après " + ", ".join(s["dependsOn"]))
            lignes.append(f"{prefixe}{'└─' if dernier else '├─'} {SIGNES.get(s['status'], '[?]')} {s['id']} {s['title']} — {' · '.join(details)}")
            branche(s["id"], prefixe + ("   " if dernier else "│  "))

    def descend(x: Dict[str, Any], ancetre: str) -> bool:
        parents = {s["id"]: s.get("parentId") for s in etat["subtasks"]}
        courant, vus = parents.get(x["id"]), set()
        while courant is not None and courant not in vus:
            if courant == ancetre:
                return True
            vus.add(courant)
            courant = parents.get(courant)
        return False

    if etat["subtasks"]:
        branche(None, "")
    else:
        lignes.append("(aucune)")

    lignes.append("")
    attente = etat.get("waiting")
    lignes.append("## Attente")
    lignes.append("aucune" if attente is None else f"{attente['kind']} — {attente['reason']} (depuis {attente.get('since')})")

    lignes.append("")
    lignes.append("## Agents déclarés (par l'orchestrateur)")
    if etat["agents"]:
        for a in etat["agents"]:
            lien = f" → {a['taskId']}" if a.get("taskId") else ""
            lignes.append(f"- {a['name']} ({a['id']}) : {a['status']} depuis {a.get('since')}{lien}")
        actifs = [a for a in etat["agents"] if a["status"] in AGENTS_ACTIFS]
        lignes.append(f"Actifs : {len(actifs)} / 5 au plus")
    else:
        lignes.append("aucun")

    observes = []
    try:
        with open(fichier_observe(chemin), encoding="utf-8") as f:
            observes = [json.loads(l) for l in f if l.strip()][-10:]
    except (OSError, ValueError):
        pass
    if observes:
        lignes.append("")
        lignes.append("## Événements observés (hôte, sans association)")
        for o in observes:
            lignes.append(f"- {o.get('at')} {o.get('event')} {o.get('agentType') or '?'} ({o.get('agentId') or '?'})")
    return "\n".join(lignes)


def observer(chemin: str) -> None:
    """Note un événement SubagentStart/Stop si une tâche est suivie ; jamais d'erreur."""
    try:
        if not os.path.exists(chemin):
            return
        evenement = json.loads(sys.stdin.read() or "null")
        if not isinstance(evenement, dict):
            return
        ligne = {
            "at": maintenant(),
            "event": evenement.get("hook_event_name"),
            "agentId": evenement.get("agent_id"),
            "agentType": evenement.get("agent_type"),
        }
        cible = fichier_observe(chemin)
        with Verrou(cible, delai=2):
            try:
                with open(cible, encoding="utf-8") as f:
                    anciennes = f.readlines()[-199:]
            except OSError:
                anciennes = []
            with open(cible + ".tmp", "w", encoding="utf-8") as f:
                f.writelines(anciennes)
                f.write(json.dumps(ligne, ensure_ascii=False) + "\n")
            os.replace(cible + ".tmp", cible)
    except Exception:
        return


# --- Ligne de commande ---------------------------------------------------------------

def donnees(arguments) -> Any:
    brut = arguments.data if arguments.data is not None else sys.stdin.read()
    try:
        return json.loads(brut)
    except ValueError as e:
        raise Erreur(INVALIDE, f"JSON invalide : {e}")


def main(argv: Optional[List[str]] = None) -> int:
    analyseur = argparse.ArgumentParser(description="Suivi de tâche de l'orchestrateur.")
    analyseur.add_argument("--file", default=None, help="fichier d'état (défaut : .claude/state/task.json du projet)")
    sous = analyseur.add_subparsers(dest="commande", required=True)
    sous.add_parser("read")
    sous.add_parser("show")
    sous.add_parser("observe")
    for nom in ("update", "patch"):
        p = sous.add_parser(nom)
        p.add_argument("--expected-revision", type=int, required=True)
        p.add_argument("--data", default=None, help="JSON ; sinon lu sur stdin")
    arguments = analyseur.parse_args(argv)
    chemin = arguments.file or chemin_par_defaut()

    if sys.stdout.encoding and sys.stdout.encoding.lower() not in {"utf-8", "utf8"}:
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    try:
        if arguments.commande == "observe":
            observer(chemin)
            return 0
        if arguments.commande == "read":
            print(json.dumps(avec_progression(lire(chemin)), ensure_ascii=False, indent=2))
            return 0
        if arguments.commande == "show":
            print(afficher(chemin))
            return 0
        charge = donnees(arguments)
        if not isinstance(charge, dict):
            raise Erreur(INVALIDE, "objet JSON attendu")
        if arguments.commande == "update":
            etat = ecrire_avec_revision(chemin, arguments.expected_revision, lambda ancien: copy.deepcopy(charge))
        else:
            etat = ecrire_avec_revision(chemin, arguments.expected_revision, lambda ancien: appliquer_patch(ancien, charge))
        print(json.dumps({"revision": etat["revision"], "progress": progression(etat["subtasks"])}, ensure_ascii=False))
        return 0
    except Erreur as e:
        print(f"task-state : {e}", file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main())
