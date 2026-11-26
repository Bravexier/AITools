#!/usr/bin/env python3
"""Garde-fou Git pour Claude Code (hook PreToolUse, matcher Bash).

Lit l'événement du hook sur l'entrée standard et analyse la commande shell,
enchaînements compris (`;`, `&&`, `||`, `|`, `&`, retours à la ligne,
`$(...)`, `bash -c "..."`), ainsi que les options globales de Git (`-C`, `-c`,
`--git-dir`...).

- `deny` : push forcé (`--force`, `-f`, `--force-with-lease`, `+refspec`,
  `--mirror`), push groupé (`--all`, `--branches`) ou vers une branche protégée
  (main, master, develop, release/*, support/*).
- `ask` : toute autre écriture Git (commit, push, reset, rebase, merge, pull,
  cherry-pick, revert, checkout, switch, restore, clean, add, rm, mv, stash hors
  list/show, création ou suppression de tag ou de branche, config et remote en
  écriture).
- Rien sinon : aucune sortie, la décision revient aux permissions habituelles.

Le hook ne renvoie jamais `allow`. Une entrée invalide ou une erreur interne ne
produit aucune décision : jamais de blocage involontaire.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from typing import Iterable, List, Optional, Tuple

Decision = Optional[Tuple[str, str]]  # (ask|deny, raison) ou None

PROTEGEES = re.compile(r"^(main|master|develop|release/.+|support/.+)$")
SEPARATEURS = {";", "&&", "||", "|", "&", "|&", "\n", "(", ")", ";;", "&;"}
ENVELOPPES = {"sudo", "env", "command", "nohup", "time", "exec", "nice", "builtin", "xargs"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}

# Options globales de Git suivies d'une valeur séparée.
GLOBALES_AVEC_VALEUR = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env", "--exec-path"}

ECRITURES_SIMPLES = {
    "commit": "commit",
    "reset": "reset",
    "rebase": "rebase",
    "merge": "merge",
    "pull": "pull",
    "cherry-pick": "cherry-pick",
    "revert": "revert",
    "checkout": "checkout",
    "switch": "switch",
    "restore": "restore",
    "clean": "clean",
    "add": "add",
    "rm": "rm",
    "mv": "mv",
}

RANG = {None: 0, "ask": 1, "deny": 2}


# --- Découpage de la commande ------------------------------------------------

def segments(commande: str) -> List[List[str]]:
    """Découpe une ligne shell en commandes simples (listes de mots)."""
    try:
        lex = shlex.shlex(commande, posix=True, punctuation_chars=";&|()<>\n")
        lex.whitespace = " \t\r"
        lex.whitespace_split = True
        lex.commenters = ""
        mots = list(lex)
    except ValueError:
        # Guillemets non fermés : découpage grossier, sans guillemets.
        mots = re.findall(r"&&|\|\||[;&|()\n]|[^\s;&|()]+", commande)
    resultat: List[List[str]] = [[]]
    for mot in mots:
        if mot in SEPARATEURS or set(mot) <= set(";&|()\n"):
            resultat.append([])
            continue
        if mot in {"<", ">", ">>", "<<", "2>", "&>"}:
            continue
        # `$(git push)` et `` `git push` `` : la commande interne est un segment.
        propre = mot
        while propre.startswith(("$", "`")):
            propre = propre[1:]
        propre = propre.rstrip("`")
        if mot.startswith("`") and resultat[-1]:
            resultat.append([])
        if propre:
            resultat[-1].append(propre)
        if mot.endswith("`") and len(mot) > 1 and not mot.startswith("`"):
            resultat.append([])
    return [s for s in resultat if s]


def commandes_git(commande: str, profondeur: int = 0) -> Iterable[Tuple[List[str], List[str]]]:
    """Rend (options globales, arguments après `git`) pour chaque appel de Git."""
    for mots in segments(commande):
        i = 0
        while i < len(mots):
            mot = mots[i]
            base = os.path.basename(mot).lower()
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", mot):  # VAR=valeur
                i += 1
                continue
            if base in ENVELOPPES:
                i += 1
                # Options des enveloppes (sudo -u x, env -i...) : on saute les tirets.
                while i < len(mots) and mots[i].startswith("-"):
                    i += 1
                continue
            if base in SHELLS and profondeur < 3:
                for j in range(i + 1, len(mots) - 1):
                    if mots[j] == "-c" or (mots[j].startswith("-") and mots[j].endswith("c") and not mots[j].startswith("--")):
                        yield from commandes_git(mots[j + 1], profondeur + 1)
                        break
                break
            if base in {"git", "git.exe"}:
                globales: List[str] = []
                i += 1
                while i < len(mots) and mots[i].startswith("-"):
                    option = mots[i]
                    globales.append(option)
                    if option in GLOBALES_AVEC_VALEUR and i + 1 < len(mots):
                        globales.append(mots[i + 1])
                        i += 2
                    else:
                        i += 1
                yield globales, mots[i:]
            break


# --- Analyse des sous-commandes ----------------------------------------------

def a_option(args: List[str], longues: Iterable[str], courtes: str = "") -> bool:
    longues = tuple(longues)
    for arg in args:
        if arg == "--":
            return False
        if arg.startswith("--"):
            if arg.split("=", 1)[0] in longues:
                return True
        elif arg.startswith("-") and len(arg) > 1 and courtes:
            if any(c in courtes for c in arg[1:]):
                return True
    return False


def positionnels(args: List[str], avec_valeur: Iterable[str] = ()) -> List[str]:
    avec_valeur = set(avec_valeur)
    rendu: List[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            rendu.extend(args[i + 1:])
            break
        if arg.startswith("-") and len(arg) > 1:
            i += 2 if arg in avec_valeur else 1
            continue
        rendu.append(arg)
        i += 1
    return rendu


def repertoire(globales: List[str], cwd: str) -> str:
    dossier = cwd or os.getcwd()
    for k, option in enumerate(globales):
        if option == "-C" and k + 1 < len(globales):
            dossier = os.path.join(dossier, globales[k + 1])
    return dossier


def branche_courante(dossier: str) -> Optional[str]:
    try:
        sortie = subprocess.run(
            ["git", "-C", dossier, "symbolic-ref", "--quiet", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3,
        )
    except Exception:
        return None
    nom = sortie.stdout.strip()
    return nom if sortie.returncode == 0 and nom else None


def cible(refspec: str) -> str:
    destination = refspec.split(":", 1)[1] if ":" in refspec else refspec
    for prefixe in ("refs/heads/", "heads/"):
        if destination.startswith(prefixe):
            destination = destination[len(prefixe):]
    return destination


def analyser_push(args: List[str], globales: List[str], cwd: str) -> Decision:
    if a_option(args, ["--force", "--force-with-lease", "--force-if-includes"], "f"):
        return "deny", "push forcé interdit"
    if a_option(args, ["--mirror"]):
        return "deny", "push --mirror interdit (forcé et groupé)"
    if a_option(args, ["--all", "--branches"]):
        return "deny", "push groupé interdit : une branche nommée par push"
    pos = positionnels(args, ["-o", "--push-option", "--repo", "--receive-pack", "--exec"])
    refspecs = pos[1:]
    for refspec in refspecs:
        if refspec.startswith("+"):
            return "deny", f"push forcé interdit (refspec {refspec})"
    implicites = [r for r in refspecs if cible(r) == "HEAD"]
    for refspec in refspecs:
        destination = cible(refspec)
        if destination != "HEAD" and PROTEGEES.match(destination):
            return "deny", f"push vers la branche protégée {destination} interdit"
    if not refspecs or implicites:
        courante = branche_courante(repertoire(globales, cwd))
        if courante and PROTEGEES.match(courante):
            return "deny", f"push implicite depuis la branche protégée {courante} interdit"
        return "ask", "push sans branche explicite : à autoriser pour ce push précis (dépôt, branche, commits)"
    return "ask", "push : à autoriser pour ce push précis (dépôt, branche, commits)"


def analyser_stash(args: List[str]) -> Decision:
    if args and args[0] in {"list", "show"}:
        return None
    return "ask", "git stash modifie l'arbre de travail"


def analyser_tag(args: List[str]) -> Decision:
    if a_option(args, ["--delete", "--annotate", "--sign", "--local-user", "--force", "--message", "--file"], "dasufmF"):
        return "ask", "création ou suppression de tag"
    lecture = a_option(args, ["--list", "--contains", "--no-contains", "--merged", "--no-merged", "--points-at", "--verify"], "lnv")
    if positionnels(args, ["--sort", "--format", "--contains", "--points-at", "--merged", "--no-merged", "--color", "--column"]) and not lecture:
        return "ask", "création de tag"
    return None


def analyser_branche(args: List[str]) -> Decision:
    if a_option(
        args,
        ["--delete", "--move", "--copy", "--force", "--set-upstream-to", "--unset-upstream", "--edit-description", "--track", "--no-track"],
        "dDmMcCfut",
    ):
        return "ask", "création, déplacement ou suppression de branche"
    lecture = a_option(
        args,
        ["--list", "--all", "--remotes", "--contains", "--no-contains", "--merged", "--no-merged", "--points-at", "--show-current", "--verbose"],
        "larv",
    )
    if positionnels(args, ["--sort", "--format", "--contains", "--no-contains", "--merged", "--no-merged", "--points-at", "--color", "--column"]) and not lecture:
        return "ask", "création de branche"
    return None


def analyser_config(args: List[str]) -> Decision:
    if args and args[0] in {"set", "unset", "rename-section", "remove-section", "edit"}:
        return "ask", "écriture de configuration Git"
    if args and args[0] in {"get", "list"}:
        return None
    if a_option(args, ["--unset", "--unset-all", "--add", "--replace-all", "--rename-section", "--remove-section", "--edit"], "e"):
        return "ask", "écriture de configuration Git"
    if a_option(args, ["--get", "--get-all", "--get-regexp", "--get-urlmatch", "--list", "--get-color", "--get-colorbool"], "l"):
        return None
    if len(positionnels(args, ["--file", "-f", "--blob", "--type", "--default", "--comment"])) >= 2:
        return "ask", "écriture de configuration Git"
    return None


def analyser_remote(args: List[str]) -> Decision:
    pos = positionnels(args)
    if pos and pos[0] in {"add", "remove", "rm", "rename", "set-url", "set-head", "set-branches", "prune"}:
        return "ask", f"git remote {pos[0]} modifie la configuration des dépôts distants"
    return None


def analyser(globales: List[str], args: List[str], cwd: str) -> Decision:
    if not args:
        return None
    sous, reste = args[0], args[1:]
    if sous == "push":
        return analyser_push(reste, globales, cwd)
    if sous in ECRITURES_SIMPLES:
        return "ask", f"git {ECRITURES_SIMPLES[sous]} écrit dans le dépôt"
    if sous == "stash":
        return analyser_stash(reste)
    if sous == "tag":
        return analyser_tag(reste)
    if sous == "branch":
        return analyser_branche(reste)
    if sous == "config":
        return analyser_config(reste)
    if sous == "remote":
        return analyser_remote(reste)
    return None


def decider(commande: str, cwd: str = "") -> Decision:
    retenue: Decision = None
    for globales, args in commandes_git(commande):
        decision = analyser(globales, args, cwd)
        if decision and RANG[decision[0]] > RANG[retenue[0] if retenue else None]:
            retenue = decision
    return retenue


def main() -> int:
    try:
        # Claude Code envoie et lit de l'UTF-8 ; sous Windows, Python prendrait cp1252.
        evenement = json.loads(sys.stdin.buffer.read().decode("utf-8") or "null")
        if not isinstance(evenement, dict) or evenement.get("tool_name") != "Bash":
            return 0
        entree = evenement.get("tool_input")
        commande = entree.get("command") if isinstance(entree, dict) else None
        if not isinstance(commande, str) or not commande.strip():
            return 0
        cwd = evenement.get("cwd") if isinstance(evenement.get("cwd"), str) else ""
        decision = decider(commande, cwd)
        if decision is None:
            return 0
        genre, raison = decision
        sys.stdout.buffer.write(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": genre,
                "permissionDecisionReason": f"git-guard : {raison}.",
            }
        }, ensure_ascii=False).encode("utf-8"))
    except Exception:  # Erreur interne : aucune décision.
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
