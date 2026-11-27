"""Cas du script de suivi : python3 -m unittest discover -s plugins/orchestration/tests"""

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest

ICI = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(ICI, "..", "scripts", "task-state.py")
HOOK = os.path.join(ICI, "..", "hooks", "session-context.py")


class Suivi(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.fichier = os.path.join(self.dossier.name, ".claude", "state", "task.json")

    def tearDown(self):
        self.dossier.cleanup()

    def cmd(self, *args, data=None, stdin=None):
        ligne = [sys.executable, SCRIPT, "--file", self.fichier, *args]
        if data is not None:
            ligne += ["--data", json.dumps(data)]
        # Le script écrit en UTF-8 ; sans encoding, Windows décoderait sa sortie en cp1252.
        sortie = subprocess.run(ligne, input=stdin or "", capture_output=True, text=True,
                                encoding="utf-8", timeout=20)
        return sortie.returncode, sortie.stdout, sortie.stderr

    def lire(self):
        code, sortie, erreur = self.cmd("read")
        self.assertEqual(code, 0, erreur)
        return json.loads(sortie)

    def initialiser(self):
        etat = {
            "task": {"title": "Livrer la fonctionnalité", "summary": "deux lots"},
            "subtasks": [
                {"id": "1", "title": "Lot A", "owner": "implementer", "weight": 1},
                {"id": "1.1", "title": "Code", "parentId": "1", "weight": 3},
                {"id": "1.2", "title": "Tests", "parentId": "1", "dependsOn": ["1.1"], "weight": 1},
                {"id": "2", "title": "Revue", "dependsOn": ["1"], "weight": 1},
            ],
        }
        code, sortie, erreur = self.cmd("update", "--expected-revision", "0", data=etat)
        self.assertEqual(code, 0, erreur)
        return json.loads(sortie)["revision"]

    def test_etat_vide(self):
        etat = self.lire()
        self.assertEqual(etat["revision"], 0)
        self.assertEqual(etat["progress"], 0)

    def test_feuille_done_fait_evoluer_le_pourcentage(self):
        rev = self.initialiser()
        self.assertEqual(self.lire()["progress"], 0)
        code, sortie, erreur = self.cmd("patch", "--expected-revision", str(rev),
                                        data={"subtasks": [{"id": "1.1", "status": "done"}]})
        self.assertEqual(code, 0, erreur)
        # feuilles 1.1 (3), 1.2 (1), 2 (1) : 3 × 100 / 5 = 60
        self.assertEqual(json.loads(sortie)["progress"], 60.0)

    def test_parent_ne_change_pas_le_pourcentage(self):
        rev = self.initialiser()
        code, _, erreur = self.cmd("patch", "--expected-revision", str(rev),
                                   data={"subtasks": [{"id": "1", "status": "in_progress", "progress": 90}]})
        self.assertEqual(code, 0, erreur)
        self.assertEqual(self.lire()["progress"], 0)

    def test_revision_perimee_rejetee(self):
        rev = self.initialiser()
        self.assertEqual(self.cmd("patch", "--expected-revision", str(rev), data={"task": {"lastMessage": "a"}})[0], 0)
        code, _, erreur = self.cmd("patch", "--expected-revision", str(rev), data={"task": {"lastMessage": "b"}})
        self.assertEqual(code, 3)
        self.assertIn("révision périmée", erreur)
        self.assertEqual(self.lire()["task"]["lastMessage"], "a")

    def test_json_accentue_sur_stdin(self):
        # Sous Windows, sans stdin en UTF-8, le script lirait « é » en cp1252 et l'abîmerait.
        etat = {"task": {"title": "Étape réussie"}, "subtasks": [{"id": "1", "title": "Lot", "weight": 1}]}
        code, _, erreur = self.cmd("update", "--expected-revision", "0", stdin=json.dumps(etat, ensure_ascii=False))
        self.assertEqual(code, 0, erreur)
        self.assertEqual(self.lire()["task"]["title"], "Étape réussie")

    def test_update_exige_aussi_la_revision(self):
        self.initialiser()
        code, _, _ = self.cmd("update", "--expected-revision", "0", data={"task": {"title": "écrasé"}})
        self.assertEqual(code, 3)

    def test_regles_de_progression(self):
        rev = self.initialiser()
        for patch in [
            {"subtasks": [{"id": "2", "status": "in_progress", "progress": 100}]},
            {"subtasks": [{"id": "2", "status": "pending", "progress": 10}]},
            {"subtasks": [{"id": "2", "status": "done", "progress": 50}]},
            {"subtasks": [{"id": "2", "status": "fini"}]},
            {"subtasks": [{"id": "2", "dependsOn": ["inconnu"]}]},
            {"subtasks": [{"id": "1", "parentId": "1.1"}]},
            {"subtasks": [{"id": "2", "weight": 0}]},
        ]:
            with self.subTest(patch=patch):
                code, _, _ = self.cmd("patch", "--expected-revision", str(rev), data=patch)
                self.assertEqual(code, 4)

    def test_progression_heritee_recalculee(self):
        rev = self.initialiser()
        _, sortie, _ = self.cmd("patch", "--expected-revision", str(rev),
                                data={"subtasks": [{"id": "2", "status": "in_progress", "progress": 40}]})
        rev = json.loads(sortie)["revision"]
        code, _, erreur = self.cmd("patch", "--expected-revision", str(rev), data={"subtasks": [{"id": "2", "status": "done"}]})
        self.assertEqual(code, 0, erreur)
        self.assertEqual([s["progress"] for s in self.lire()["subtasks"] if s["id"] == "2"], [100])

    def test_status_since_change_avec_le_statut(self):
        rev = self.initialiser()
        avant = {s["id"]: s["statusSince"] for s in self.lire()["subtasks"]}
        self.cmd("patch", "--expected-revision", str(rev), data={"subtasks": [{"id": "2", "status": "in_progress", "statusSince": "2000-01-01T00:00:00Z"}]})
        apres = {s["id"]: s["statusSince"] for s in self.lire()["subtasks"]}
        self.assertEqual(apres["2"], "2000-01-01T00:00:00Z")
        self.assertEqual(apres["1.1"], avant["1.1"])

    def test_done_seulement_quand_tout_est_fini(self):
        rev = self.initialiser()
        code, _, erreur = self.cmd("patch", "--expected-revision", str(rev), data={"task": {"status": "done"}})
        self.assertEqual(code, 4)
        self.assertIn("feuilles non terminées", erreur)
        feuilles = [{"id": i, "status": "done"} for i in ("1.1", "1.2", "2")]
        _, sortie, _ = self.cmd("patch", "--expected-revision", str(rev), data={
            "subtasks": feuilles, "agents": [{"id": "a1", "name": "reviewer", "status": "running", "taskId": "2"}],
            "waiting": {"kind": "user", "reason": "valider la revue"}})
        rev = json.loads(sortie)["revision"]
        code, _, erreur = self.cmd("patch", "--expected-revision", str(rev), data={"task": {"status": "done"}})
        self.assertEqual(code, 4)
        self.assertIn("agents actifs", erreur)
        self.assertIn("attente ouverte", erreur)
        code, sortie, erreur = self.cmd("patch", "--expected-revision", str(rev), data={
            "task": {"status": "done"}, "waiting": None, "agents": [{"id": "a1", "status": "completed"}]})
        self.assertEqual(code, 0, erreur)
        self.assertEqual(json.loads(sortie)["progress"], 100.0)

    def test_attente_typee(self):
        rev = self.initialiser()
        self.assertEqual(self.cmd("patch", "--expected-revision", str(rev), data={"waiting": {"kind": "boss", "reason": "x"}})[0], 4)
        code, _, _ = self.cmd("patch", "--expected-revision", str(rev), data={"waiting": {"kind": "external", "reason": "CI"}})
        self.assertEqual(code, 0)
        self.assertEqual(self.lire()["waiting"]["kind"], "external")
        self.assertTrue(self.lire()["waiting"]["since"])

    def test_retrait_par_id(self):
        rev = self.initialiser()
        code, _, erreur = self.cmd("patch", "--expected-revision", str(rev), data={"remove": {"subtasks": ["2"]}})
        self.assertEqual(code, 0, erreur)
        self.assertNotIn("2", [s["id"] for s in self.lire()["subtasks"]])

    def test_show(self):
        rev = self.initialiser()
        self.cmd("patch", "--expected-revision", str(rev), data={
            "subtasks": [{"id": "1.1", "status": "done"}],
            "agents": [{"id": "a1", "name": "implementer", "status": "running", "taskId": "1.2"}],
            "waiting": {"kind": "agents", "reason": "tests du lot A"}})
        self.cmd("observe", stdin=json.dumps({"hook_event_name": "SubagentStart", "agent_id": "x9", "agent_type": "implementer"}))
        code, sortie, erreur = self.cmd("show")
        self.assertEqual(code, 0, erreur)
        for attendu in ["# Livrer la fonctionnalité", "60.0 %", "├─ [ ] 1 Lot A", "[x] 1.1 Code", "après 1.1",
                        "agents — tests du lot A", "implementer (a1) : running", "Actifs : 1 / 5",
                        "Événements observés", "SubagentStart implementer (x9)"]:
            self.assertIn(attendu, sortie)

    def test_observe_sans_tache_ne_cree_rien(self):
        code, _, _ = self.cmd("observe", stdin="pas du json")
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(os.path.dirname(self.fichier)))

    def test_verrou_abandonne_est_repris(self):
        rev = self.initialiser()
        verrou = self.fichier + ".lock"
        open(verrou, "w").close()
        os.utime(verrou, (0, 0))
        code, _, erreur = self.cmd("patch", "--expected-revision", str(rev), data={"task": {"lastMessage": "x"}})
        self.assertEqual(code, 0, erreur)
        self.assertFalse(os.path.exists(verrou))


class Sessions(unittest.TestCase):
    """Un suivi par session : le fichier d'état dépend de l'identifiant de session, pas du seul dossier."""

    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.tmp = self.dossier.name
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("TASK_STATE_FILE", "CLAUDE_CODE_SESSION_ID", "ORCHESTRATION_DISABLED")}
        self.env["CLAUDE_PROJECT_DIR"] = self.tmp

    def tearDown(self):
        self.dossier.cleanup()

    def lancer(self, script, *args, stdin="", env=None):
        sortie = subprocess.run([sys.executable, script, *args], input=stdin, capture_output=True, text=True,
                                encoding="utf-8", timeout=20, cwd=self.tmp, env=env or self.env)
        return sortie.returncode, sortie.stdout, sortie.stderr

    def cmd(self, options, *args, data=None, stdin="", env=None):
        args = list(args)
        if data is not None:
            args += ["--data", json.dumps(data)]
        return self.lancer(SCRIPT, *options, *args, stdin=stdin, env=env)

    def options_de_session(self, sid):
        """Options de task-state.py que le hook SessionStart montre à une session."""
        entree = json.dumps({"session_id": sid, "hook_event_name": "SessionStart", "source": "startup", "cwd": self.tmp})
        code, sortie, erreur = self.lancer(HOOK, stdin=entree)
        self.assertEqual(code, 0, erreur)
        trouve = re.search(r'task-state\.py"([^`]*)`', sortie)
        self.assertIsNotNone(trouve, "commande task-state.py introuvable dans le contexte de session")
        return shlex.split(trouve.group(1))

    def etat(self, titre):
        return {"task": {"title": titre}, "subtasks": [{"id": "1", "title": "Lot", "weight": 1}]}

    def test_deux_sessions_du_meme_dossier_ont_chacune_leur_suivi(self):
        options = {sid: self.options_de_session(sid) for sid in ("sess-a", "sess-b")}
        for sid, opts in options.items():
            code, _, erreur = self.cmd(opts, "update", "--expected-revision", "0", data=self.etat("Tâche " + sid))
            self.assertEqual(code, 0, f"{sid} : {erreur}")
        for sid, opts in options.items():
            code, sortie, erreur = self.cmd(opts, "read")
            self.assertEqual(code, 0, erreur)
            self.assertEqual(json.loads(sortie)["task"]["title"], "Tâche " + sid)

    def test_evenement_observe_va_a_la_session_qui_l_emet(self):
        opts_a = self.options_de_session("sess-a")
        code, _, erreur = self.cmd(opts_a, "update", "--expected-revision", "0", data=self.etat("Tâche A"))
        self.assertEqual(code, 0, erreur)
        for sid, agent, type_agent in (("sess-a", "x1", "implementer"), ("sess-b", "x2", "reviewer")):
            entree = json.dumps({"session_id": sid, "hook_event_name": "SubagentStart",
                                 "agent_id": agent, "agent_type": type_agent})
            self.assertEqual(self.cmd([], "observe", stdin=entree)[0], 0)
        code, sortie, erreur = self.cmd(opts_a, "show")
        self.assertEqual(code, 0, erreur)
        self.assertIn("SubagentStart implementer (x1)", sortie)
        self.assertNotIn("reviewer", sortie)

    def test_identifiant_de_session_invalide_refuse_sans_rien_creer(self):
        for sid in ("../x", ""):
            with self.subTest(session=sid):
                code, _, erreur = self.cmd(["--session", sid], "update", "--expected-revision", "0", data=self.etat("X"))
                self.assertEqual(code, 2)
                self.assertFalse(os.path.exists(os.path.join(self.tmp, ".claude")))
                self.assertIn("1 à 128", erreur or "")

    def test_sans_session_ni_variable_ancien_chemin(self):
        code, _, erreur = self.cmd([], "update", "--expected-revision", "0", data=self.etat("Ancien"))
        self.assertEqual(code, 0, erreur)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, ".claude", "state", "task.json")))

    def test_variable_de_session_donne_le_dossier_de_session(self):
        env = {**self.env, "CLAUDE_CODE_SESSION_ID": "sess-c"}
        code, _, erreur = self.cmd([], "update", "--expected-revision", "0", data=self.etat("Var"), env=env)
        self.assertEqual(code, 0, erreur)
        etat = os.path.join(self.tmp, ".claude", "state")
        self.assertTrue(os.path.exists(os.path.join(etat, "sessions", "sess-c", "task.json")))
        self.assertFalse(os.path.exists(os.path.join(etat, "task.json")))

    def test_show_signale_l_ancien_suivi_du_dossier(self):
        code, _, erreur = self.cmd([], "update", "--expected-revision", "0", data=self.etat("Ancien"))
        self.assertEqual(code, 0, erreur)
        code, sortie, erreur = self.cmd(["--session", "sess-d"], "show")
        self.assertEqual(code, 0, erreur)
        self.assertIn(".claude/state/task.json", sortie.replace("\\", "/"))
        self.assertIn("--file", sortie)

    def fichiers_sous(self, racine):
        return sorted(os.path.relpath(os.path.join(d, f), racine) for d, _, fs in os.walk(racine) for f in fs)

    def test_observe_avec_session_invalide_n_ecrit_nulle_part(self):
        code, _, erreur = self.cmd([], "update", "--expected-revision", "0", data=self.etat("Ancien"))
        self.assertEqual(code, 0, erreur)
        avant = self.fichiers_sous(self.tmp)
        entree = json.dumps({"session_id": "../x", "hook_event_name": "SubagentStart",
                             "agent_id": "x1", "agent_type": "implementer"})
        code, _, erreur = self.cmd([], "observe", stdin=entree)
        self.assertEqual(code, 0, erreur)
        self.assertEqual(self.fichiers_sous(self.tmp), avant)
        self.assertFalse([f for f in self.fichiers_sous(self.tmp) if f.endswith("observed.jsonl")])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, ".claude", "state", "sessions")))

    def test_variable_de_session_invalide_ignoree(self):
        env = {**self.env, "CLAUDE_CODE_SESSION_ID": "../x"}
        code, _, erreur = self.cmd([], "update", "--expected-revision", "0", data=self.etat("Ancien"), env=env)
        self.assertEqual(code, 0, erreur)
        etat = os.path.join(self.tmp, ".claude", "state")
        self.assertTrue(os.path.exists(os.path.join(etat, "task.json")))
        self.assertFalse(os.path.exists(os.path.join(etat, "sessions")))

    def test_file_puis_variable_fichier_l_emportent_sur_la_session(self):
        explicite = os.path.join(self.tmp, "f", "explicite.json")
        via_env = os.path.join(self.tmp, "e", "env.json")
        env = {**self.env, "CLAUDE_CODE_SESSION_ID": "sess-e"}
        code, _, erreur = self.cmd(["--file", explicite, "--session", "sess-f"], "update",
                                   "--expected-revision", "0", data=self.etat("F"), env={**env, "TASK_STATE_FILE": via_env})
        self.assertEqual(code, 0, erreur)
        self.assertTrue(os.path.exists(explicite))
        self.assertFalse(os.path.exists(via_env))
        code, _, erreur = self.cmd(["--session", "sess-f"], "update", "--expected-revision", "0",
                                   data=self.etat("E"), env={**env, "TASK_STATE_FILE": via_env})
        self.assertEqual(code, 0, erreur)
        self.assertTrue(os.path.exists(via_env))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, ".claude", "state", "sessions")))


if __name__ == "__main__":
    unittest.main()
