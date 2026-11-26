"""Structure du plugin : agents, hooks, contexte. python3 -m unittest discover -s plugins/orchestration/tests"""

import json
import os
import re
import subprocess
import sys
import unittest

RACINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OUTILS_PERMIS = {
    "explorer": {"Read", "Grep", "Glob"},
    "implementer": {"Read", "Grep", "Glob", "Edit", "Write", "Bash"},
    "reviewer": {"Read", "Grep", "Glob", "Bash"},
    "tester": {"Read", "Grep", "Glob", "Bash"},
    "git-operator": {"Read", "Bash"},
}


def frontmatter(chemin):
    texte = open(chemin, encoding="utf-8").read()
    bloc = re.match(r"^---\n(.*?)\n---\n", texte, re.S).group(1)
    return dict(ligne.split(": ", 1) for ligne in bloc.splitlines() if ": " in ligne), texte


class Agents(unittest.TestCase):
    def test_outils_minimaux_sans_delegation(self):
        dossier = os.path.join(RACINE, "agents")
        noms = sorted(f[:-3] for f in os.listdir(dossier) if f.endswith(".md"))
        self.assertEqual(noms, sorted(OUTILS_PERMIS))
        for nom in noms:
            with self.subTest(agent=nom):
                champs, texte = frontmatter(os.path.join(dossier, nom + ".md"))
                self.assertEqual(champs["name"], nom)
                self.assertTrue(champs["description"])
                outils = {o.strip() for o in champs["tools"].split(",")}
                self.assertEqual(outils, OUTILS_PERMIS[nom])
                self.assertFalse(outils & {"Agent", "Task"})
                self.assertIn("Statut : terminé | partiel | bloqué", texte)
                self.assertIn("Suite :", texte)
                self.assertIn("bloqué — règle métier à confirmer", texte)


class Hooks(unittest.TestCase):
    def test_declarations(self):
        hooks = json.load(open(os.path.join(RACINE, "hooks", "hooks.json"), encoding="utf-8"))["hooks"]
        self.assertEqual(hooks["PreToolUse"][0]["matcher"], "Bash")
        for evenement in ("SessionStart", "PreToolUse", "SubagentStart", "SubagentStop"):
            commande = hooks[evenement][0]["hooks"][0]["command"]
            for chemin in re.findall(r'\$\{CLAUDE_PLUGIN_ROOT\}/([^"]+)"', commande):
                self.assertTrue(os.path.exists(os.path.join(RACINE, chemin)), chemin)

    def contexte(self, **env):
        sortie = subprocess.run([sys.executable, os.path.join(RACINE, "hooks", "session-context.py")],
                                capture_output=True, text=True, timeout=20, env={**os.environ, **env})
        return sortie.returncode, sortie.stdout

    def test_contexte_de_session(self):
        code, texte = self.contexte(ORCHESTRATION_DISABLED="")
        self.assertEqual(code, 0)
        self.assertIn("Tu es l'orchestrateur", texte)
        self.assertNotIn("@RACINE@", texte)
        self.assertIn("/scripts/task-state.py", texte)

    def test_contexte_desactivable(self):
        self.assertEqual(self.contexte(ORCHESTRATION_DISABLED="1"), (0, ""))

    def test_lanceur_python(self):
        sortie = subprocess.run(["sh", os.path.join(RACINE, "scripts", "py.sh"), "-c", "print('ok')"],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual((sortie.returncode, sortie.stdout.strip()), (0, "ok"))


if __name__ == "__main__":
    unittest.main()
