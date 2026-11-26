"""Cas du script de suivi : python3 -m unittest discover -s plugins/orchestration/tests"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

ICI = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(ICI, "..", "scripts", "task-state.py")


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


if __name__ == "__main__":
    unittest.main()
