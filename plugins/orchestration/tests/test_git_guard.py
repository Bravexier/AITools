"""Cas du hook git-guard : python3 -m unittest discover -s plugins/orchestration/tests"""

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

ICI = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(ICI, "..", "hooks", "git-guard.py")
sys.path.insert(0, os.path.join(ICI, "..", "hooks"))

import importlib.util

_spec = importlib.util.spec_from_file_location("git_guard", HOOK)
git_guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(git_guard)


# Comme Claude Code : rien n'impose l'UTF-8 à Python (sous Windows, il prendrait cp1252).
SANS_ENCODAGE_FORCE = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}


def lancer_brut(entree: str):
    """Lance le hook comme Claude Code : JSON en UTF-8 sur stdin, sortie lue en UTF-8
    (octets invalides remplacés par U+FFFD). Rend (code de sortie, texte lu)."""
    sortie = subprocess.run([sys.executable, HOOK], input=entree.encode("utf-8"), capture_output=True,
                            timeout=20, env=SANS_ENCODAGE_FORCE)
    return sortie.returncode, sortie.stdout.decode("utf-8", "replace")


def lancer(entree: str):
    """Lance le hook comme Claude Code : rend (code de sortie, décision ou None)."""
    code, texte = lancer_brut(entree)
    if not texte.strip():
        return code, None
    return code, json.loads(texte)["hookSpecificOutput"]["permissionDecision"]


def evenement(commande, cwd="", tool_name="Bash"):
    # Comme Claude Code : les caractères non ASCII arrivent tels quels, pas en \uXXXX.
    return json.dumps({"tool_name": tool_name, "tool_input": {"command": commande}, "cwd": cwd}, ensure_ascii=False)


class DepotTemporaire:
    """Un dépôt Git jetable sur une branche donnée, pour les push implicites."""

    def __init__(self, branche, prefixe=None):
        self.branche = branche
        self.prefixe = prefixe

    def __enter__(self):
        self.dossier = tempfile.TemporaryDirectory(prefix=self.prefixe)
        subprocess.run(["git", "init", "-q", "-b", self.branche, self.dossier.name], check=True)
        return self.dossier.name

    def __exit__(self, *exc):
        self.dossier.cleanup()


class CasDeLIssue(unittest.TestCase):
    """Les cas exigés par les critères d'acceptation, via le vrai processus."""

    def test_ask(self):
        for commande in ["git reset --hard", "git -C x clean -fd", "git checkout -- .", "git branch -D x"]:
            with self.subTest(commande=commande):
                self.assertEqual(lancer(evenement(commande)), (0, "ask"))

    def test_deny(self):
        for commande in ["git push --force", "git push origin +b", "git push origin main"]:
            with self.subTest(commande=commande):
                self.assertEqual(lancer(evenement(commande)), (0, "deny"))

    def test_aucune_decision(self):
        for commande in ["git status", "git log --oneline", "git fetch origin", "ls -la", "echo git push --force",
                         'git commit -m "x"', "git push origin feature/x", "git -C x rebase main"]:
            with self.subTest(commande=commande):
                self.assertEqual(lancer(evenement(commande)), (0, None))

    def test_rebase_puis_tests_sans_demande(self):
        # La commande qui arrêtait une session : rebase sur la base, puis les tests du lot.
        commande = ('cd /c/Git/Depot/.claude/worktrees/lot && git rebase origin/dev 2>&1 | tail -3'
                    ' && git log --oneline -2 && cd client && node --test test/a.test.mjs test/b.test.mjs 2>&1'
                    ' | grep -E "(tests|pass|fail|skipped) [0-9]+|✕" | head -20')
        self.assertEqual(lancer(evenement(commande)), (0, None))

    def test_entree_invalide(self):
        for entree in ["", "pas du json", "[]", "null", json.dumps({"tool_name": "Bash"}),
                       json.dumps({"tool_name": "Bash", "tool_input": {"command": 42}}),
                       json.dumps({"tool_name": "Read", "tool_input": {"file_path": "x"}})]:
            with self.subTest(entree=entree):
                self.assertEqual(lancer(entree), (0, None))


class Encodage(unittest.TestCase):
    """Claude Code envoie et lit de l'UTF-8, même sous Windows où Python prend cp1252 par défaut."""

    def test_raison_lisible(self):
        # Cassé si le hook écrit dans l'encodage local : Claude Code lirait « push forc� interdit ».
        code, texte = lancer_brut(evenement("git push --force"))
        self.assertEqual(code, 0)
        self.assertIn("forcé", json.loads(texte)["hookSpecificOutput"]["permissionDecisionReason"])

    def test_push_implicite_depuis_un_dossier_accentue(self):
        # Cassé si le hook lit stdin dans l'encodage local : le cwd « …dépôt… » devient « …dÃ©pÃ´t… »,
        # git n'y trouve pas la branche courante et le push depuis main n'est plus refusé.
        with DepotTemporaire("main", prefixe="dépôt-") as depot:
            self.assertEqual(lancer(evenement("git push", depot)), (0, "deny"))


class Analyse(unittest.TestCase):
    def d(self, commande, cwd=""):
        decision = git_guard.decider(commande, cwd)
        return decision[0] if decision else None

    def test_enchainements(self):
        self.assertEqual(self.d("git status && git reset --hard"), "ask")
        self.assertEqual(self.d("git status; git push -f origin x"), "deny")
        self.assertEqual(self.d("git log | head\ngit push origin develop"), "deny")
        self.assertEqual(self.d("cd a || git push origin release/1.2"), "deny")
        self.assertEqual(self.d("git add a && git push --force origin x"), "deny")
        self.assertEqual(self.d('bash -c "git push origin main"'), "deny")
        self.assertEqual(self.d("echo $(git push origin master)"), "deny")
        self.assertEqual(self.d("FOO=1 sudo git reset --hard"), "ask")
        self.assertEqual(self.d("/usr/bin/git reset --hard"), "ask")

    def test_options_globales(self):
        self.assertEqual(self.d("git -c user.name=x reset --hard"), "ask")
        self.assertEqual(self.d("git --no-pager -C sub log"), None)
        self.assertEqual(self.d("git --git-dir .git push origin support/2"), "deny")

    def test_push(self):
        self.assertEqual(self.d("git push --force-with-lease origin x"), "deny")
        self.assertEqual(self.d("git push -uf origin x"), "deny")
        self.assertEqual(self.d("git push --all origin"), "deny")
        self.assertEqual(self.d("git push --mirror"), "deny")
        self.assertEqual(self.d("git push origin HEAD:refs/heads/main"), "deny")
        self.assertEqual(self.d("git push origin --delete main"), "deny")
        self.assertEqual(self.d("git push origin :master"), "deny")
        self.assertEqual(self.d("git push -u origin feature/x"), None)
        self.assertEqual(self.d("git push origin mainline"), None)
        self.assertEqual(self.d('git commit -m "pas un push --force"'), None)

    def test_push_implicite(self):
        with DepotTemporaire("main") as depot:
            self.assertEqual(self.d("git push", depot), "deny")
            self.assertEqual(self.d("git push origin HEAD", depot), "deny")
        with DepotTemporaire("feature/x") as depot:
            self.assertEqual(self.d("git push", depot), None)

    def test_ecritures_courantes(self):
        # Aucune demande : une fenêtre par commande arrêtait les sessions sans rien protéger.
        for commande in ["git add f", "git commit -m x", "git commit --amend", "git rebase origin/dev",
                         "git rebase --continue", "git merge x", "git pull", "git cherry-pick a", "git revert a",
                         "git checkout x", "git checkout -b y", "git checkout --ours f", "git checkout -- f",
                         "git switch -c y", "git restore f", "git restore --staged .", "git reset --soft HEAD~1",
                         "git reset f", "git clean -nd", "git rm f", "git mv a b", "git stash", "git stash pop",
                         "git tag v1", "git tag -d v1", "git branch nouvelle", "git branch -d x", "git branch -m y",
                         "git config user.name x", "git config --unset a.b", "git config set a.b c",
                         "git remote add o url", "git remote set-url o url"]:
            with self.subTest(commande=commande):
                self.assertEqual(self.d(commande), None)

    def test_destructrices(self):
        for commande in ["git reset --hard", "git reset -q --hard origin/dev", "git clean -fd", "git clean -fdx",
                         "git checkout .", "git checkout -- .", "git checkout -q HEAD -- .", "git checkout -f x",
                         "git checkout --force", "git switch -f x", "git switch --discard-changes x",
                         "git restore .", "git restore --source HEAD~1 -- .", "git restore --staged --worktree .",
                         "git restore -SW :/", "git stash drop", "git stash clear", "git branch -D x",
                         "git branch -d -f x", "git branch --delete --force x"]:
            with self.subTest(commande=commande):
                self.assertEqual(self.d(commande), "ask")

    def test_consultations(self):
        for commande in ["git diff", "git diff --cached", "git show HEAD", "git stash list", "git stash show",
                         "git tag", "git tag -l 'v*'", "git branch", "git branch -a", "git branch --list 'f*'",
                         "git branch --show-current", "git config --get user.name", "git config user.name",
                         "git config --list", "git config get a.b", "git remote", "git remote -v",
                         "git remote get-url origin", "git rev-parse HEAD", "git blame f"]:
            with self.subTest(commande=commande):
                self.assertEqual(self.d(commande), None)

    def test_guillemets_non_fermes(self):
        self.assertEqual(self.d('git clean -fd "x'), "ask")
        self.assertEqual(self.d("git status '"), None)


# --- Outil PowerShell ---------------------------------------------------------

def b64(s):
    """Valeur de -EncodedCommand : base64 de l'UTF-16LE."""
    return base64.b64encode(s.encode("utf-16-le")).decode("ascii")


SEPARATEURS = [
    ("git status; git push --force origin x", "deny"),
    ("git log --oneline | Select-Object -First 3", None),
    ("git status\ngit reset --hard", "ask"),
    ("git fetch && git push origin main", "deny"),
    ("git status || git push --force", "deny"),
    ("git push --force origin x &", "deny"),
    ("git push `\n  --force origin x", "deny"),                     # continuation
    ("git add .; if ($?) { git push origin main }", "deny"),
    ("git push --% --force origin x", "deny"),
    ("git log --% --format=%H", None),
]
APPELS = [
    ("& git push --force", "deny"),
    ("& 'C:\\Program Files\\Git\\cmd\\git.exe' push --force", "deny"),
    ('& "C:\\Program Files\\Git\\cmd\\git.exe" reset --hard', "ask"),
    ("C:\\Git\\cmd\\git.exe push origin main", "deny"),
    ("GIT.EXE push --force", "deny"),
    ("Git reset --hard", "ask"),
    ("git -C C:\\x\\depot push origin main", "deny"),
]
GUILLEMETS = [
    ("git commit -m 'C''est fait ; git push --force'", None),
    ('git commit -m "a `" ; git push --force"', None),
    ('git commit -m "a "" ; git push --force"', None),
    ('git commit -m "ne pas faire git push --force, c\'est interdit"', None),
    ('git commit -m "x" ; git push --force', "deny"),
    ('git commit -m "$(git push --force)"', "deny"),
    ('git commit -m "`$(git push --force)"', None),
    ("git commit -m 'pas $(git push --force)'", None),
    ("Write-Host a`; git push --force", None),
    ("Write-Host `' ; git push --force", "deny"),
    ('git reset --hard "x', "ask"),                                       # non fermée
    ("Write-Host 'a ; git push --force", "deny"),                      # non fermée : prudence
]
HERE_STRINGS = [
    ("git commit -m @'\nCorrige l'état ; git push --force origin main\n'@", None),
    ("git commit -m @'\n$(git push --force)\n'@", None),
    ('git commit -m @"\nTitre\ngit push --force\n"@', None),
    ('git commit -m @"\n$(git push --force)\n"@', "deny"),
    ("$m = @'\nx ; git push --force\n'@\ngit commit -m $m", None),
    ("git commit -m @'\nTitre\n  '@\ngit push --force", "deny"),      # fermeture tolérée : prudence
]
GROUPES = [
    ("$(git push --force)", "deny"),
    ("@(git push origin main)", "deny"),
    ("(git push --force)", "deny"),
    ("[void](git push origin main)", "deny"),
    ("Invoke-Command { git push --force }", "deny"),
    ("if ($true) { git reset --hard }", "ask"),
    ("1..3 | ForEach-Object { git push origin main }", "deny"),
    ("if (git diff --quiet) { 'propre' } else { git stash clear }", "ask"),
    ("git push origin $(git branch --show-current) --force", "deny"),
    ("git push origin +$(git branch --show-current)", "deny"),
    ("git push -u origin (git branch --show-current)", None),         # branche calculée : inconnue
    ("git push origin feature/x,main", "deny"),                        # virgule
]
AFFECTATIONS = [
    ("$r = git push --force", "deny"),
    ("$null = git reset --hard", "ask"),
    ("$r=git push --force", "deny"),
]
COMMENTAIRES = [
    ("# Pousser l'état\ngit push --force", "deny"),
    ("# l'état\ngit push --force\n# c'est tout", "deny"),
    ("<# l'état #> git push --force", "deny"),
    ("git status # c'est propre", None),
    ("git status # git push --force", "deny"),                         # prudence : commentaire analysé
]
IMBRIQUEES = [
    ('cmd /c "git push --force"', "deny"),
    ('cmd.exe /C "cd C:\\x && C:\\Git\\cmd\\git.exe push origin main"', "deny"),
    ("cmd /c echo git push --force", None),
    ('bash -c "git push --force"', "deny"),
    ("& 'C:\\Program Files\\Git\\bin\\bash.exe' -c 'git push origin main'", "deny"),
    ('pwsh -NoProfile -Command "git push --force"', "deny"),
    ('powershell.exe -c "Set-Location C:\\x; git reset --hard"', "ask"),
    ("powershell -NoProfile -EncodedCommand " + b64("git push origin main"), "deny"),
    ("pwsh -enc " + b64("git status"), None),
    ('pwsh -c "Write-Output \'git push --force\'"', None),
    ('Invoke-Expression "git push --force"', "deny"),
    ("iex 'git reset --hard'", "ask"),
    ("Invoke-Expression -Command 'git status'", None),
    ("Start-Process git -ArgumentList 'push','--force' -NoNewWindow -Wait", "deny"),
    ('Start-Process -FilePath \'C:\\Program Files\\Git\\cmd\\git.exe\' -ArgumentList "push origin main"', "deny"),
    ("Start-Process git 'reset --hard'", "ask"),
    ("Start-Process git -ArgumentList '-C','x','status'", None),
]
LECTURES = [
    ("git status", None), ("git log --oneline", None),
    ('Write-Output "git push --force"', None), ("Write-Host 'git push --force'", None),
    ("echo git push --force", None), ("git log --oneline | Select-String 'push --force'", None),
]
COLLES = [  # `(` ou `{` collé à la fin d'un mot : un argument à part, comme dans PowerShell
    ("git push origin main(1)", "deny"),
    ("git push --force(1) origin x", "deny"),
    ("git push origin main{x}", "deny"),
    ("git push origin main(1)(2)", "deny"),
]
LITTERAUX = [  # groupement qui ne contient que des chaînes littérales : il vaut ces chaînes
    ("iex ('git push origin main')", "deny"),
    ('Invoke-Expression ("git push --force")', "deny"),
    ("git push origin ('main')", "deny"),
    ("Start-Process git -ArgumentList @('push','--force')", "deny"),
    ('git push origin ("$b")', None),
]
CRLF = [  # backtick puis CRLF : PowerShell 5.1 coupe l'instruction, PowerShell 7 peut continuer la ligne
    ("echo a`\r\ngit push origin main", "deny"),
    ("git push`\r\n--force origin x", "deny"),
]


class PowerShell(unittest.TestCase):
    """Découpage PowerShell et programmes reconnus : decider(commande, cwd, shell="powershell")."""

    def d(self, commande, cwd=""):
        decision = git_guard.decider(commande, cwd, shell="powershell")
        return decision[0] if decision else None

    def verifier(self, cas):
        for commande, attendu in cas:
            with self.subTest(commande=commande):
                self.assertEqual(self.d(commande), attendu)

    def test_separateurs(self):
        self.verifier(SEPARATEURS)

    def test_appels(self):
        self.verifier(APPELS)

    def test_guillemets(self):
        self.verifier(GUILLEMETS)

    def test_here_strings(self):
        self.verifier(HERE_STRINGS)

    def test_groupes(self):
        self.verifier(GROUPES)

    def test_affectations(self):
        self.verifier(AFFECTATIONS)

    def test_commentaires(self):
        self.verifier(COMMENTAIRES)

    def test_imbriquees(self):
        self.verifier(IMBRIQUEES)

    def test_lectures(self):
        self.verifier(LECTURES)

    def test_guillemets_typographiques(self):
        # Le vrai analyseur de PowerShell les traite comme ' et " (vérifié avec Parser::ParseInput).
        self.verifier([("Write-Host 'a\u2019 ; git push --force ; \u2019b'", "deny"),
                       ("git commit -m \u201ca ; git push --force\u201d", None)])

    def test_redirections(self):
        # Une redirection n'est pas un argument de git : elle ne change pas la décision.
        with DepotTemporaire("main") as depot:
            self.assertEqual(self.d("git push", depot), "deny")
            self.assertEqual(self.d("git push 2>&1", depot), "deny")
            self.assertEqual(git_guard.decider("git push origin *> $null", depot, shell="powershell"),
                             git_guard.decider("git push origin", depot))
            self.assertEqual(self.d("git push --force origin x 2>&1 | Out-String", depot), "deny")

    def test_segments(self):
        # Même forme que segments() : commandes simples, mots sans guillemets, échappements appliqués.
        s = git_guard.segments_powershell
        self.assertEqual(s("git status; git push origin feature/x,main"),
                         [["git", "status"], ["git", "push", "origin", "feature/x", "main"]])
        self.assertEqual(s("git commit -m 'C''est' \"a `\" b\" a`;b"),
                         [["git", "commit", "-m", "C'est", 'a " b', "a;b"]])
        self.assertEqual(s("git push `\n  --force origin x 2>&1 *> $null"), [["git", "push", "--force", "origin", "x"]])
        self.assertEqual(s("$r=git push"), [["$r", "=", "git", "push"], ["git", "push"]])
        # Comme le vrai analyseur : backtick dans un mot, here-string aux fins de ligne CRLF.
        self.assertEqual(s("git commit -m a` b @'\r\nTitre\r\n'@"), [["git", "commit", "-m", "a b", "Titre"]])

    def test_cmd_recoit_les_guillemets_de_powershell(self):
        # PowerShell entoure de guillemets un argument qui contient un blanc : cmd ne le découpe pas.
        self.verifier([('cmd /c echo "a & git push --force"', None), ('cmd /c "echo a & git push --force"', "deny")])

    def test_guillemet_final_non_ferme(self):
        # Le guillemet seul ne laisse pas de mot vide : le push reste implicite, refusé depuis main.
        with DepotTemporaire("main") as depot:
            self.assertEqual(self.d('git push origin "', depot), "deny")

    def test_groupement_colle_a_un_mot(self):
        # Comme le vrai analyseur : `(` ou `{` finit le mot ; `$(` y reste ; `main@(1)` donne `main@` puis `(1)`.
        self.verifier(COLLES)
        calcul = git_guard.VALEUR_CALCULEE
        self.assertEqual(git_guard.segments_powershell("x main@(1) +$(a)"),
                         [["1"], ["a"], ["x", "main@", calcul, "+" + calcul]])

    def test_groupement_de_chaines_litterales(self):
        self.verifier(LITTERAUX)
        self.assertEqual(git_guard.segments_powershell("x ('a', \"b\") @(@'\nc\n'@) $('d')e y$('f','g')"),
                         [["x", "a", "b", "c", "d", "e", "yf g"]])

    def test_imbrication_profonde(self):
        # Au-delà de PROFONDEUR_MAX, la partie trop profonde n'est pas analysée : elle demande confirmation,
        # sans exception ; la décision la plus sévère l'emporte.
        trop_profond = ("ask", "commande trop imbriquée pour être analysée")
        for ouvrant in ("$(", "(", "{"):
            with self.subTest(ouvrant=ouvrant):
                debut = time.perf_counter()
                self.assertEqual(git_guard.decider(ouvrant * 600, "", shell="powershell"), trop_profond)
                self.assertEqual(self.d("git push --force; " + ouvrant * 600), "deny")
                self.assertLess(time.perf_counter() - debut, 1)
        self.assertEqual(self.d("(" * 600 + ")" * 600 + "; git push --force"), "deny")
        self.assertEqual(self.d("(" * 600 + "git status" + ")" * 600), "ask")
        self.assertEqual(self.d("(" * 50 + "git status" + ")" * 50), None)

    def test_backtick_crlf_deux_lectures(self):
        self.verifier(CRLF)

    def test_here_strings_non_fermees_en_temps_lineaire(self):
        for entete in ("@'\n", '@"\n'):
            with self.subTest(entete=entete):
                debut = time.perf_counter()
                self.assertEqual(self.d(entete * 10000 + "git push --force"), "deny")
                self.assertLess(time.perf_counter() - debut, 1)


class ProcessusPowerShell(unittest.TestCase):
    """L'outil PowerShell de Claude Code, par le vrai processus du hook."""

    def test_decisions(self):
        for commande, attendu in [("git push --force", "deny"),
                                  ("git reset --hard", "ask"),
                                  ("git commit -m @'\nTitre : git push --force\n'@", None),
                                  ("git status", None)]:
            with self.subTest(commande=commande):
                self.assertEqual(lancer(evenement(commande, tool_name="PowerShell")), (0, attendu))

    def test_entree_invalide(self):
        self.assertEqual(lancer(json.dumps({"tool_name": "PowerShell", "tool_input": {"command": 42}})), (0, None))

    def test_autre_outil_ignore(self):
        self.assertEqual(lancer(evenement("git push --force", tool_name="Terminal")), (0, None))

    def test_push_implicite_depuis_un_dossier_accentue(self):
        with DepotTemporaire("main", prefixe="dépôt-") as depot:
            self.assertEqual(lancer(evenement("git push", depot, tool_name="PowerShell")), (0, "deny"))

    def test_commande_trop_imbriquee(self):
        code, texte = lancer_brut(evenement("$(" * 600, tool_name="PowerShell"))
        sortie = json.loads(texte or "{}").get("hookSpecificOutput", {})
        self.assertEqual((code, sortie.get("permissionDecision"), sortie.get("permissionDecisionReason")),
                         (0, "ask", "git-guard : commande trop imbriquée pour être analysée."))


if __name__ == "__main__":
    unittest.main()
