"""Cas du hook git-guard : python3 -m unittest discover -s plugins/orchestration/tests"""

import json
import os
import subprocess
import sys
import tempfile
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


def evenement(commande, cwd=""):
    # Comme Claude Code : les caractères non ASCII arrivent tels quels, pas en \uXXXX.
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": commande}, "cwd": cwd}, ensure_ascii=False)


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
        for commande in ['git commit -m "x"', "git push origin feature/x", "git reset --hard", "git -C x rebase main"]:
            with self.subTest(commande=commande):
                self.assertEqual(lancer(evenement(commande)), (0, "ask"))

    def test_deny(self):
        for commande in ["git push --force", "git push origin +b", "git push origin main"]:
            with self.subTest(commande=commande):
                self.assertEqual(lancer(evenement(commande)), (0, "deny"))

    def test_aucune_decision(self):
        for commande in ["git status", "git log --oneline", "git fetch origin", "ls -la", "echo git push --force"]:
            with self.subTest(commande=commande):
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
        self.assertEqual(self.d("git status && git commit -m x"), "ask")
        self.assertEqual(self.d("git status; git push -f origin x"), "deny")
        self.assertEqual(self.d("git log | head\ngit push origin develop"), "deny")
        self.assertEqual(self.d("cd a || git push origin release/1.2"), "deny")
        self.assertEqual(self.d("git add a && git push --force origin x"), "deny")
        self.assertEqual(self.d('bash -c "git push origin main"'), "deny")
        self.assertEqual(self.d("echo $(git push origin master)"), "deny")
        self.assertEqual(self.d("FOO=1 sudo git commit"), "ask")
        self.assertEqual(self.d("/usr/bin/git commit"), "ask")

    def test_options_globales(self):
        self.assertEqual(self.d("git -c user.name=x commit -m y"), "ask")
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
        self.assertEqual(self.d("git push -u origin feature/x"), "ask")
        self.assertEqual(self.d("git push origin mainline"), "ask")
        self.assertEqual(self.d('git commit -m "pas un push --force"'), "ask")

    def test_push_implicite(self):
        with DepotTemporaire("main") as depot:
            self.assertEqual(self.d("git push", depot), "deny")
            self.assertEqual(self.d("git push origin HEAD", depot), "deny")
        with DepotTemporaire("feature/x") as depot:
            self.assertEqual(self.d("git push", depot), "ask")

    def test_ecritures(self):
        for commande in ["git merge x", "git pull", "git cherry-pick a", "git revert a", "git checkout x",
                         "git switch -c y", "git restore f", "git clean -fd", "git add f", "git rm f", "git mv a b",
                         "git stash", "git stash pop", "git tag v1", "git tag -d v1", "git branch nouvelle",
                         "git branch -D x", "git config user.name x", "git config --unset a.b",
                         "git config set a.b c", "git remote add o url", "git remote set-url o url"]:
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
        self.assertEqual(self.d('git commit -m "x'), "ask")
        self.assertEqual(self.d("git status '"), None)


if __name__ == "__main__":
    unittest.main()
