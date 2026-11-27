#!/usr/bin/env python3
"""Garde-fou Git pour Claude Code (hook PreToolUse, outils Bash et PowerShell).

Lit l'événement du hook sur l'entrée standard et analyse la commande shell,
enchaînements compris (`;`, `&&`, `||`, `|`, `&`, retours à la ligne,
`$(...)`, `bash -c "..."`), ainsi que les options globales de Git (`-C`, `-c`,
`--git-dir`...). Pour l'outil PowerShell : chaînes et here-strings, groupements
`(...)`, `$(...)`, `@(...)`, `{...}`, `@{...}`, affectations, commentaires,
redirections, et les programmes qui lancent du code (`cmd /c`, `bash -c`,
`pwsh -Command` ou `-EncodedCommand`, `Invoke-Expression`, `Start-Process`).

- `deny` : push forcé (`--force`, `-f`, `--force-with-lease`, `+refspec`,
  `--mirror`), push groupé (`--all`, `--branches`) ou vers une branche protégée
  (main, master, develop, release/*, support/*).
- `ask` : ce qui efface du travail non enregistré ou une branche, sans retour
  simple : `reset --hard`, `clean` (hors `--dry-run`), `checkout` et `switch`
  forcés (`--force`, `--discard-changes`), `checkout` ou `restore` de tout
  l'arbre (`.`, `:/`, `*`), `stash drop` et `stash clear`, suppression forcée de
  branche (`branch -D`) ; pour l'outil PowerShell, une commande trop imbriquée
  pour être analysée (plus de PROFONDEUR_MAX groupements).
- Rien sinon : aucune sortie, la décision revient aux permissions habituelles.
  Les écritures courantes en font partie (add, commit, rebase, merge, pull,
  checkout d'une branche ou de fichiers nommés, push vers une branche non
  protégée…) : demander à chacune arrêtait les sessions sans rien protéger.

Le hook ne renvoie jamais `allow`. Une entrée invalide ou une erreur interne ne
produit aucune décision : jamais de blocage involontaire.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shlex
import subprocess
import sys
from collections import deque
from typing import Dict, Iterable, List, Optional, Set, Tuple

Decision = Optional[Tuple[str, str]]  # (ask|deny, raison) ou None

PROTEGEES = re.compile(r"^(main|master|develop|release/.+|support/.+)$")
SEPARATEURS = {";", "&&", "||", "|", "&", "|&", "\n", "(", ")", ";;", "&;"}
ENVELOPPES = {"sudo", "env", "command", "nohup", "time", "exec", "nice", "builtin", "xargs"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}

# Options globales de Git suivies d'une valeur séparée.
GLOBALES_AVEC_VALEUR = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env", "--exec-path"}

# Chemins qui désignent tout l'arbre de travail : les restaurer efface toutes les modifications.
TOUT_L_ARBRE = {".", "./", ".\\", ":/", "*"}

RANG = {None: 0, "ask": 1, "deny": 2}

# --- PowerShell ---
# Valeur d'un groupement `$(…)`, `(…)`, `{…}`… dans la commande qui l'entoure : inconnue, et
# jamais une branche protégée (`+$(…)` reste un push forcé, `release/$(…)` une branche protégée).
VALEUR_CALCULEE = "«calcul»"
# Imbrication des groupements, `$(…)` des chaînes compris (une chaîne ne s'imbrique que par eux) : au-delà,
# la partie trop profonde est sautée sans être analysée et compte pour le segment PARTIE_NON_ANALYSEE.
PROFONDEUR_MAX = 100
PARTIE_NON_ANALYSEE = "«trop profond»"
DECISION_TROP_PROFOND: Decision = ("ask", "commande trop imbriquée pour être analysée")
GUILLEMETS_SIMPLES = "'‘’‚‛"  # PowerShell accepte aussi ‘ ’ ‚ ‛
GUILLEMETS_DOUBLES = '"“”„'  # et “ ” „
ENTETE_HERE_STRING = re.compile("@([" + GUILLEMETS_SIMPLES + GUILLEMETS_DOUBLES + "])[ \t]*\r?\n")
# Fermeture en début de ligne ; des espaces avant sont tolérés : elle arrive plus tôt, jamais plus tard.
FIN_HERE_STRING_SIMPLE = re.compile("\r?\n[ \t]*[" + GUILLEMETS_SIMPLES + "]@")
FIN_HERE_STRING_DOUBLE = re.compile("\r?\n[ \t]*[" + GUILLEMETS_DOUBLES + "]@")
AFFECTATION = re.compile(r"(?:\?\?|[-+*/%])?=")
REDIRECTION = re.compile(r"[1-6*]?>>?(&[1-6])?")  # `n>&m` n'a pas de cible
FIN_DE_LIGNE = re.compile(r"[\r\n]")
FIN_COMMENTAIRE = re.compile("#>")
BLANCS = re.compile(r"\s*")
# cmd.exe : séparateurs, redirections, blancs et mots (guillemets doubles et `^` compris).
JETON_CMD = re.compile(r'(?P<separateur>[&|()\r\n])|(?P<redirection>[<>]>?(?:&\d)?)|[ \t]+'
                       r'|(?P<mot>(?:"[^"]*"?|\^.|[^\s"^&|()<>])+)', re.S)
# Paramètres de Start-Process, noms complets et alias (Get-Command Start-Process), communs compris.
START_PROCESS_AVEC_VALEUR = {
    "-filepath", "-pspath", "-argumentlist", "-args", "-credential", "-runas", "-workingdirectory", "-verb",
    "-windowstyle", "-environment", "-redirectstandarderror", "-rse", "-redirectstandardinput", "-rsi",
    "-redirectstandardoutput", "-rso", "-erroraction", "-ea", "-warningaction", "-wa", "-informationaction",
    "-infa", "-errorvariable", "-ev", "-warningvariable", "-wv", "-informationvariable", "-iv",
    "-outvariable", "-ov", "-outbuffer", "-ob", "-pipelinevariable", "-pv",
}
START_PROCESS_INTERRUPTEURS = {
    "-loaduserprofile", "-lup", "-nonewwindow", "-nnw", "-passthru", "-wait", "-usenewenvironment",
    "-verbose", "-vb", "-debug", "-db",
}


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


class LecteurPowerShell:
    """Lit du code PowerShell, de `debut` à `fin` : `commandes` reçoit les commandes simples, celles des
    groupements comprises ; `commentaires`, les bornes du texte des commentaires, que segments_powershell
    analyse à part. `continuation_crlf` : lecture de PowerShell 7, où un backtick suivi de CRLF continue
    la ligne même au milieu d'un mot (PowerShell 5.1 n'échappe que le CR et finit l'instruction)."""

    def __init__(self, texte: str, continuation_crlf: bool = False, debut: int = 0, fin: Optional[int] = None,
                 echecs: Optional[Dict[Tuple[re.Pattern, int], int]] = None):
        self.texte = texte
        self.i = debut
        self.fin = len(texte) if fin is None else fin  # borne de lecture, réduite le temps du contenu d'une here-string
        self.continuation_crlf = continuation_crlf
        self.profondeur = 0  # groupements ouverts
        self.commandes: List[List[str]] = []
        self.commentaires: List[Tuple[int, int]] = []
        self.non_fermees: Set[int] = set()  # chaînes "…" déjà vues sans fin : relecture évitée
        self.passages: Dict[Tuple[int, str, int], int] = {}  # (début, fermeture, borne) → fin, pour passer()
        # (motif de fermeture, borne) → début d'une recherche vaine ; commun aux lecteurs du même texte
        self.echecs: Dict[Tuple[re.Pattern, int], int] = {} if echecs is None else echecs

    def car(self, decalage: int = 0) -> str:
        j = self.i + decalage
        return self.texte[j] if j < self.fin else ""

    def chercher(self, motif: re.Pattern, debut: int) -> Optional[re.Match]:
        """motif.search(texte, debut, fin), sans refaire plus loin une recherche qui a échoué : une fermeture
        absente l'est aussi après. Des ouvrants non fermés répétés restent ainsi en temps linéaire."""
        cle = (motif, self.fin)
        if debut >= self.echecs.get(cle, self.fin + 1):
            return None
        trouve = motif.search(self.texte, debut, self.fin)
        if trouve is None:
            self.echecs[cle] = debut
        return trouve

    def debut_de_chaine(self) -> bool:
        c = self.car()
        return c != "" and (c in GUILLEMETS_SIMPLES + GUILLEMETS_DOUBLES
                            or (c == "@" and ENTETE_HERE_STRING.match(self.texte, self.i, self.fin) is not None))

    def lire(self, fermeture: str = "") -> None:
        """Lit des commandes jusqu'à `fermeture` (`)` ou `}`, consommée) ou la fin du texte."""
        commande: List[str] = []
        queues: List[int] = []  # après un opérateur d'affectation, la suite est aussi une commande
        cible = False  # le prochain mot est la cible d'une redirection
        while self.i < self.fin:
            c, suivant = self.car(), self.car(1)
            if fermeture and c == fermeture:
                self.i += 1
                break
            if c in ";|&\r\n)}":  # séparateurs ; un `)` ou `}` sans ouvrant en est un aussi
                self.ranger(commande, queues)
                commande, queues, cible = [], [], False
                self.i += 1
            elif c.isspace() or c == ",":  # `main,dev` : deux arguments
                self.i += 1
            elif c == "`" and (not suivant or suivant.isspace()):  # continuation de ligne
                self.i += 3 if suivant == "\r" and self.car(2) == "\n" else 2
            elif c == "#":  # commentaire jusqu'à la fin de la ligne
                fin = self.chercher(FIN_DE_LIGNE, self.i)
                fin = fin.start() if fin else self.fin
                self.commentaires.append((self.i + 1, fin))
                self.i = fin
            elif c == "<" and suivant == "#":  # commentaire <# … #>
                fin = self.chercher(FIN_COMMENTAIRE, self.i + 2)
                fin = fin.start() if fin else self.fin
                self.commentaires.append((self.i + 2, fin))
                self.i = min(fin + 2, self.fin)
            elif c in "({" or (c in "$@" and suivant == "(") or (c == "@" and suivant == "{"):
                valeurs = self.groupement()  # un mot qui commence par un groupement s'arrête après lui
                if cible:
                    cible = False
                else:
                    commande += valeurs
            else:
                redirection = REDIRECTION.match(self.texte, self.i, self.fin)
                if redirection:  # ni l'opérateur ni sa cible ne sont des arguments
                    self.i = redirection.end()
                    cible = not redirection.group(1)
                    continue
                mot, brut = self.mot()
                if mot is None:
                    continue
                if cible:
                    cible = False
                    continue
                for morceau in couper_affectation(mot, premier=not commande) if brut else [mot]:
                    commande.append(morceau)
                    if brut and AFFECTATION.fullmatch(morceau):
                        queues.append(len(commande))
        self.ranger(commande, queues)

    def ranger(self, commande: List[str], queues: List[int]) -> None:
        for debut in [0] + queues:
            if commande[debut:]:
                self.commandes.append(commande[debut:])

    def groupement(self) -> List[str]:
        """Lit le groupement `(…)`, `$(…)`, `@(…)`, `{…}` ou `@{…}` qui commence ici. S'il ne contient que
        des chaînes littérales (accolades exclues), rend ces chaînes ; sinon lit son contenu comme des
        commandes et rend [VALEUR_CALCULEE]. Au-delà de PROFONDEUR_MAX, le contenu est sauté sans analyse."""
        self.i += 1 if self.car() in "({" else 2
        fermeture = ")" if self.texte[self.i - 1] == "(" else "}"
        valeurs = self.liste_litterale() if fermeture == ")" else None
        if valeurs is not None:
            return valeurs
        if self.profondeur >= PROFONDEUR_MAX:
            self.passer(fermeture)
            self.commandes.append([PARTIE_NON_ANALYSEE])
        else:
            self.profondeur += 1
            self.lire(fermeture)
            self.profondeur -= 1
        return [VALEUR_CALCULEE]

    def liste_litterale(self) -> Optional[List[str]]:
        """Après l'ouvrant d'un `(…)`, `$(…)` ou `@(…)` : s'il ne contient qu'une chaîne littérale ('…',
        @'…'@, ou "…" et @"…"@ sans `$`) ou une liste de telles chaînes séparées par des virgules, rend ces
        chaînes, fermeture consommée ; sinon None, sans rien consommer ni analyser."""
        depart = self.i
        valeurs: List[str] = []
        while True:
            self.i = BLANCS.match(self.texte, self.i, self.fin).end()
            valeur = self.chaine(litterale=True) if self.debut_de_chaine() else None
            if valeur is None:
                break
            valeurs.append(valeur)
            self.i = BLANCS.match(self.texte, self.i, self.fin).end()
            if self.car() == ")":
                self.i += 1
                return valeurs
            if self.car() != ",":
                break
            self.i += 1
        self.i = depart
        return None

    def passer(self, fermeture: str) -> None:
        """Saute le contenu d'un groupement jusqu'à sa fermeture (consommée) ou la fin du texte, sans l'analyser
        ni s'appeler récursivement ; chaînes, here-strings et commentaires y sont reconnus à peu près. Une
        chaîne non fermée relue comme du code ramène au même groupement : le passage déjà fait est réutilisé."""
        cle = (self.i, fermeture, self.fin)
        if cle in self.passages:
            self.i = self.passages[cle]
            return
        attendues = [fermeture]  # fermetures attendues ; '"' : dans une chaîne "…", où seuls `$(` et le guillemet comptent
        while attendues and self.i < self.fin:
            c, suivant = self.car(), self.car(1)
            if c == "`":
                self.i += 2
            elif c == "$" and suivant == "(":
                attendues.append(")")
                self.i += 2
            elif attendues[-1] == '"':
                if c in GUILLEMETS_DOUBLES:
                    if suivant and suivant in GUILLEMETS_DOUBLES:  # "" : un guillemet
                        self.i += 1
                    else:
                        attendues.pop()
                self.i += 1
            elif c in GUILLEMETS_DOUBLES:
                attendues.append('"')
                self.i += 1
            elif self.debut_de_chaine():  # '…' ou here-string, sans analyse ; non fermée : son ouvrant seul
                self.chaine(litterale=True)
            elif c == "<" and suivant == "#":
                fin = self.chercher(FIN_COMMENTAIRE, self.i + 2)
                self.i = fin.end() if fin else self.fin
            elif c == "#" and (self.texte[self.i - 1].isspace() or self.texte[self.i - 1] in ";|&,(){}"):
                fin = self.chercher(FIN_DE_LIGNE, self.i)
                self.i = fin.start() if fin else self.fin
            elif c in "({":
                attendues.append(")" if c == "(" else "}")
                self.i += 1
            else:
                if c == attendues[-1]:
                    attendues.pop()
                self.i += 1
        self.passages[cle] = self.i

    def mot(self) -> Tuple[Optional[str], bool]:
        """Lit un mot ; rend sa valeur (guillemets retirés, échappements appliqués, `$(…)` remplacé par
        sa valeur), ou None s'il n'avait qu'un guillemet non fermé, et s'il est brut (sans guillemets,
        échappement ni groupement). Comme dans PowerShell, un `(` ou un `{` le finit et ouvre un autre
        argument (`main(1)` : `main` puis `(1)`) ; `$(…)` y reste (`+$(…)`)."""
        morceaux: List[str] = []  # chaque morceau lu y entre, même une chaîne vide
        brut = True
        debut = self.i
        while self.i < self.fin:
            c, suivant = self.car(), self.car(1)
            if c.isspace() or c in ";|&,(){}" or (c == "`" and not suivant):
                break
            depart = self.i
            if c == "`":  # le caractère suivant est pris tel quel, même un blanc ou un retour à la ligne
                if suivant == "\r" and self.car(2) == "\n" and self.continuation_crlf:
                    break  # PowerShell 7 : continuation de ligne, qui finit le mot
                morceaux.append(suivant)  # devant CRLF, PowerShell 5.1 n'échappe que le CR : le LF finit l'instruction
                self.i += 2
                brut = False
                continue
            if self.debut_de_chaine():
                valeur = self.chaine()
                brut = False
                if valeur is None:  # non fermée : PowerShell la refuserait ; la suite est lue comme du code
                    continue
                morceaux.append(valeur)
            elif c == "$" and suivant == "(":  # jamais en début de mot : lire() s'en charge
                morceaux.append(" ".join(self.groupement()))
                brut = False
                continue
            elif c == "$" and suivant == "{" and self.texte.find("}", self.i, self.fin) >= 0:  # ${nom}
                fin = self.texte.find("}", self.i, self.fin) + 1
                morceaux.append(self.texte[self.i:fin])
                self.i = fin
                continue
            else:
                morceaux.append(c)
                self.i += 1
                continue
            if depart == debut:  # un mot qui commence par une chaîne s'arrête après elle
                break
        return ("".join(morceaux) if morceaux else None), brut

    def chaine(self, litterale: bool = False) -> Optional[str]:
        """Lit une chaîne ou une here-string ; rend sa valeur, ou None si elle n'est pas fermée
        (seul l'ouvrant est alors consommé). `litterale` : rien n'y est analysé, et une chaîne
        double qui contient un `$` rend aussi None."""
        debut = self.i
        entete = ENTETE_HERE_STRING.match(self.texte, self.i, self.fin) if self.car() == "@" else None
        if entete:
            simple = entete.group(1) in GUILLEMETS_SIMPLES
            fermeture = self.chercher(FIN_HERE_STRING_SIMPLE if simple else FIN_HERE_STRING_DOUBLE, entete.end() - 1)
            if not fermeture:
                self.i += 2
                return None
            if simple:  # contenu littéral, jamais analysé
                valeur: Optional[str] = self.texte[entete.end():fermeture.start()]
            else:  # seuls ses `$(…)` sont analysés
                borne, self.fin, self.i = self.fin, fermeture.start(), entete.end()
                valeur = self.extensible(here_string=True, litterale=litterale)
                self.fin = borne
            self.i = fermeture.end()
            return valeur
        if self.car() in GUILLEMETS_SIMPLES:  # littérale ; '' vaut une apostrophe
            morceaux: List[str] = []
            j = self.i + 1
            while j < self.fin:
                if self.texte[j] in GUILLEMETS_SIMPLES:
                    if j + 1 < self.fin and self.texte[j + 1] in GUILLEMETS_SIMPLES:
                        morceaux.append(self.texte[j])
                        j += 2
                        continue
                    self.i = j + 1
                    return "".join(morceaux)
                morceaux.append(self.texte[j])
                j += 1
            self.i += 1
            return None
        if litterale:
            self.i += 1
            return None if debut in self.non_fermees else self.extensible(here_string=False, litterale=True)
        avant = (len(self.commandes), len(self.commentaires))
        if debut not in self.non_fermees:
            self.i += 1
            valeur = self.extensible(here_string=False)
            if valeur is not None:
                return valeur
            self.non_fermees.add(debut)
        # Non fermée : ce qui a été lu dedans sera relu comme du code.
        del self.commandes[avant[0]:], self.commentaires[avant[1]:]
        self.i = debut + 1
        return None

    def extensible(self, here_string: bool, litterale: bool = False) -> Optional[str]:
        """Contenu d'une chaîne "…" ou @"…"@ : le backtick échappe, `""` vaut `"` (hors here-string),
        `$(…)` est lu comme des commandes. Rend None si la chaîne "…" n'est pas fermée ou, si `litterale`,
        dès un `$`, même échappé (rien n'est alors analysé)."""
        morceaux: List[str] = []
        while self.i < self.fin:
            c = self.car()
            if litterale and "$" in (c, self.car(1) if c == "`" else ""):
                return None
            if c == "`":
                morceaux.append(self.car(1))
                self.i += 2
            elif c == "$" and self.car(1) == "(":
                morceaux.append(" ".join(self.groupement()))
            elif c in GUILLEMETS_DOUBLES and not here_string:
                self.i += 1
                suivant = self.car()
                if not suivant or suivant not in GUILLEMETS_DOUBLES:
                    return "".join(morceaux)
                morceaux.append(c)  # "" : un guillemet
                self.i += 1
            else:
                morceaux.append(c)
                self.i += 1
        return "".join(morceaux) if here_string else None


def couper_affectation(mot: str, premier: bool) -> List[str]:
    """Coupe un mot brut autour de son opérateur d'affectation (`$r=git` → `$r`, `=`, `git`) s'il
    commence par `$` ou par l'opérateur (`$r =git`), ou s'il ouvre la commande (`@{x=git push}`)."""
    morceaux: List[str] = []
    operateur = AFFECTATION.search(mot)
    while operateur and (premier or mot.startswith("$") or operateur.start() == 0):
        morceaux += [x for x in (mot[:operateur.start()], operateur.group()) if x]
        mot, premier = mot[operateur.end():], False
        operateur = AFFECTATION.search(mot)
    return morceaux + ([mot] if mot else [])


def segments_powershell(commande: str) -> List[List[str]]:
    """Découpe du code PowerShell en commandes simples (listes de mots), comme segments() : guillemets
    retirés, échappements appliqués, redirections retirées ; le contenu des groupements est analysé et
    chacun compte pour un argument (VALEUR_CALCULEE), ou pour ses chaînes s'il ne contient que des chaînes
    littérales ; le texte des commentaires est analysé à part ; une partie trop profonde compte pour
    [PARTIE_NON_ANALYSEE]. Un backtick suivi de CRLF se lit des deux façons (PowerShell 5.1 finit
    l'instruction, PowerShell 7 peut continuer la ligne) : les commandes des deux lectures sont rendues."""
    resultat: List[List[str]] = []
    echecs: Dict[Tuple[re.Pattern, int], int] = {}  # recherches vaines, communes aux lecteurs du même texte
    for continuation_crlf in ((False, True) if "`\r\n" in commande else (False,)):
        a_lire = deque([(0, len(commande))])
        while a_lire:  # un commentaire est plus court que le texte qui le contient : la boucle finit
            debut, fin = a_lire.popleft()
            lecteur = LecteurPowerShell(commande, continuation_crlf, debut, fin, echecs)
            lecteur.lire()
            resultat += lecteur.commandes
            a_lire += lecteur.commentaires
    return resultat


def segments_cmd(texte: str) -> List[List[str]]:
    """Découpe une ligne de cmd.exe : séparateurs `&`, `&&`, `||`, `|` (et parenthèses, retours à la
    ligne) ; seuls les guillemets doubles comptent ; `^` échappe ; l'antislash est littéral ;
    redirections retirées."""
    commandes: List[List[str]] = [[]]
    cible = False
    for jeton in JETON_CMD.finditer(texte):
        mot = jeton.group("mot")
        if jeton.group("separateur"):
            commandes.append([])
            cible = False
        elif jeton.group("redirection"):
            cible = "&" not in jeton.group("redirection")
        elif mot and cible:
            cible = False
        elif mot and not (len(mot) == 1 and mot.isdigit() and texte[jeton.end():jeton.end() + 1] in ("<", ">")):
            commandes[-1].append("".join(a or b or c for a, b, c in re.findall(r'"([^"]*)"?|\^(.)|([^"^])', mot, re.S)))
    return [c for c in commandes if c]


def decouper_arguments(texte: str) -> List[str]:
    """Redécoupe une ligne d'arguments Windows aux espaces, guillemets doubles respectés."""
    return [m.replace('"', "") for m in re.findall(r'(?:"[^"]*"?|[^\s"])+', texte)]


def nom_programme(mot: str) -> str:
    """Nom d'un programme sous Windows : sans dossier (`\\` ou `/`), sans `.exe`, en minuscules."""
    nom = re.split(r"[\\/]", mot)[-1].lower()
    return nom[:-4] if nom.endswith(".exe") else nom


def decoder_commande(valeur: str) -> str:
    """Valeur de -EncodedCommand (base64 de l'UTF-16LE), ou "" si elle est illisible."""
    try:
        return base64.b64decode(valeur, validate=True).decode("utf-16-le")
    except ValueError:  # binascii.Error et UnicodeDecodeError en dérivent
        return ""


def lancement_start_process(args: List[str]) -> List[str]:
    """[programme, arguments…] lancés par Start-Process, ou [] : le programme est -FilePath ou le
    1er positionnel ; ses arguments, -ArgumentList (-Args) ou le 2e positionnel, joints puis
    redécoupés. Un tableau `'a','b'` arrive en plusieurs mots : la valeur court jusqu'au paramètre suivant."""
    connus = START_PROCESS_AVEC_VALEUR | START_PROCESS_INTERRUPTEURS
    programme: Optional[str] = None
    arguments: List[str] = []
    i = 0
    while i < len(args):
        nom = args[i].lower()
        if nom in ("-argumentlist", "-args") or (programme is not None and not arguments and not nom.startswith("-")):
            debut = i + 1 if nom.startswith("-") else i
            i = debut
            while i < len(args) and args[i].lower() not in connus:
                i += 1
            arguments = args[debut:i]
        elif nom in ("-filepath", "-pspath"):
            programme = args[i + 1] if i + 1 < len(args) else None
            i += 2
        elif nom in START_PROCESS_AVEC_VALEUR:
            i += 2
        elif not nom.startswith("-") and programme is None:
            programme = args[i]
            i += 1
        else:  # interrupteur, paramètre inconnu ou abrégé
            i += 1
    return [programme] + decouper_arguments(" ".join(arguments)) if programme else []


def code_de_l_option_c(args: List[str]) -> Optional[str]:
    """Le code passé à un shell POSIX par `-c` (ou `-xc`…), sinon None."""
    for j in range(len(args) - 1):
        if args[j] == "-c" or (args[j].startswith("-") and args[j].endswith("c") and not args[j].startswith("--")):
            return args[j + 1]
    return None


def appel_git(args: List[str]) -> Tuple[List[str], List[str]]:
    """Sépare les options globales de Git (avant la sous-commande) de ses arguments."""
    globales: List[str] = []
    i = 0
    while i < len(args) and args[i].startswith("-"):
        option = args[i]
        globales.append(option)
        if option in GLOBALES_AVEC_VALEUR and i + 1 < len(args):
            globales.append(args[i + 1])
            i += 2
        else:
            i += 1
    return globales, args[i:]


def appels_powershell(mots: List[str], profondeur: int) -> Iterable[Tuple[List[str], List[str]]]:
    """Appels de Git d'une commande simple de PowerShell (ou de cmd) : `git` ou `git.exe`, chemin
    complet accepté, et les programmes qui lancent du code, jusqu'à la même profondeur que bash -c."""
    i = 0
    while i < len(mots):
        mot, nom = mots[i], nom_programme(mots[i])
        if mot == "." or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", mot):  # `. script`, VAR=valeur
            i += 1
            continue
        if nom in ENVELOPPES:
            i += 1
            while i < len(mots) and mots[i].startswith("-"):
                i += 1
            continue
        args = mots[i + 1:]
        if nom == "git":
            yield appel_git(args)
        elif profondeur >= 3:
            pass
        elif nom in SHELLS:
            code = code_de_l_option_c(args)
            if code is not None:
                yield from commandes_git(code, profondeur + 1)
        elif nom == "cmd":  # cmd [/s /q…] /c|/k <commande>
            j = 0
            while j < len(args) and args[j].startswith("/") and args[j].lower() not in ("/c", "/k"):
                j += 1
            if j < len(args) and args[j].lower() in ("/c", "/k"):
                # PowerShell met entre guillemets un argument qui contient un blanc ; si la ligne
                # commence par un guillemet, cmd retire ce guillemet et le dernier.
                texte = " ".join('"' + a + '"' if not a or re.search(r"\s", a) else a for a in args[j + 1:])
                if texte.startswith('"'):
                    dernier = texte.rfind('"')
                    texte = texte[1:dernier] + texte[dernier + 1:]
                for sous in segments_cmd(texte):
                    yield from appels_powershell(sous, profondeur + 1)
        elif nom in ("pwsh", "powershell"):
            for j, arg in enumerate(args):
                option = arg.lower()
                if option in ("-e", "-ec") or (len(option) > 2 and "-encodedcommand".startswith(option)):
                    if j + 1 < len(args):
                        yield from commandes_git(decoder_commande(args[j + 1]), profondeur + 1, "powershell")
                elif not arg.startswith("-"):  # -Command, -c, ou la commande sans option de PowerShell 5.1
                    yield from commandes_git(" ".join(args[j:]), profondeur + 1, "powershell")
        elif nom in ("invoke-expression", "iex"):
            code = [a for a in args if not (len(a) > 1 and "-command".startswith(a.lower()))]
            yield from commandes_git(" ".join(code), profondeur + 1, "powershell")
        elif nom in ("start-process", "start", "saps"):
            yield from appels_powershell(lancement_start_process(args), profondeur + 1)
        break


def commandes_git(commande: str, profondeur: int = 0, shell: str = "posix") -> Iterable[Tuple[List[str], List[str]]]:
    """Rend (options globales, arguments après `git`) pour chaque appel de Git ; `shell` vaut
    "posix" (outil Bash) ou "powershell" (outil PowerShell). Une partie de code PowerShell trop
    profonde pour être analysée rend ([PARTIE_NON_ANALYSEE], [])."""
    if shell == "powershell":
        for mots in segments_powershell(commande):
            if mots == [PARTIE_NON_ANALYSEE]:
                yield [PARTIE_NON_ANALYSEE], []
            else:
                yield from appels_powershell(mots, profondeur)
        return
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
                code = code_de_l_option_c(mots[i + 1:])
                if code is not None:
                    yield from commandes_git(code, profondeur + 1)
                break
            if base in {"git", "git.exe"}:
                yield appel_git(mots[i + 1:])
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
    return None


def analyser_reset(args: List[str]) -> Decision:
    if a_option(args, ["--hard"]):
        return "ask", "git reset --hard efface les modifications non enregistrées"
    return None


def analyser_clean(args: List[str]) -> Decision:
    if a_option(args, ["--dry-run"], "n"):
        return None
    return "ask", "git clean supprime les fichiers non suivis"


def analyser_checkout(args: List[str]) -> Decision:
    if a_option(args, ["--force"], "f"):
        return "ask", "git checkout --force écrase les modifications locales"
    if any(arg in TOUT_L_ARBRE for arg in args):
        return "ask", "git checkout de tout l'arbre efface les modifications non enregistrées"
    return None


def analyser_switch(args: List[str]) -> Decision:
    if a_option(args, ["--force", "--discard-changes"], "f"):
        return "ask", "git switch forcé efface les modifications locales"
    return None


def analyser_restore(args: List[str]) -> Decision:
    if a_option(args, ["--staged"], "S") and not a_option(args, ["--worktree"], "W"):
        return None  # l'index seul : les fichiers ne changent pas
    if any(arg in TOUT_L_ARBRE for arg in args):
        return "ask", "git restore de tout l'arbre efface les modifications non enregistrées"
    return None


def analyser_stash(args: List[str]) -> Decision:
    if args and args[0] in {"drop", "clear"}:
        return "ask", f"git stash {args[0]} efface du travail remisé"
    return None


def analyser_branche(args: List[str]) -> Decision:
    if a_option(args, [], "D") or (a_option(args, ["--delete"], "d") and a_option(args, ["--force"], "f")):
        return "ask", "suppression forcée de branche"
    return None


# Sous-commandes qui peuvent effacer du travail. Les autres écritures (add, commit, rebase, merge…) ne décident rien.
DESTRUCTRICES = {
    "reset": analyser_reset,
    "clean": analyser_clean,
    "checkout": analyser_checkout,
    "switch": analyser_switch,
    "restore": analyser_restore,
    "stash": analyser_stash,
    "branch": analyser_branche,
}


def analyser(globales: List[str], args: List[str], cwd: str) -> Decision:
    if not args:
        return None
    sous, reste = args[0], args[1:]
    if sous == "push":
        return analyser_push(reste, globales, cwd)
    if sous in DESTRUCTRICES:
        return DESTRUCTRICES[sous](reste)
    return None


def decider(commande: str, cwd: str = "", shell: str = "posix") -> Decision:
    retenue: Decision = None
    for globales, args in commandes_git(commande, shell=shell):
        # Partie trop profonde : demande de confirmation. La décision la plus sévère l'emporte.
        decision = DECISION_TROP_PROFOND if globales == [PARTIE_NON_ANALYSEE] else analyser(globales, args, cwd)
        if decision and RANG[decision[0]] > RANG[retenue[0] if retenue else None]:
            retenue = decision
    return retenue


def main() -> int:
    try:
        # Claude Code envoie et lit de l'UTF-8 ; sous Windows, Python prendrait cp1252.
        evenement = json.loads(sys.stdin.buffer.read().decode("utf-8") or "null")
        if not isinstance(evenement, dict) or evenement.get("tool_name") not in ("Bash", "PowerShell"):
            return 0
        shell = "powershell" if evenement["tool_name"] == "PowerShell" else "posix"
        entree = evenement.get("tool_input")
        commande = entree.get("command") if isinstance(entree, dict) else None
        if not isinstance(commande, str) or not commande.strip():
            return 0
        cwd = evenement.get("cwd") if isinstance(evenement.get("cwd"), str) else ""
        decision = decider(commande, cwd, shell)
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
