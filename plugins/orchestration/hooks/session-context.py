#!/usr/bin/env python3
"""Hook SessionStart : ajoute au contexte les règles permanentes de l'orchestrateur.

Équivaut à un CLAUDE.md commun à tous les projets. Le `session_id` reçu sur stdin
(JSON, facultatif) est ajouté à la commande de suivi. Rien n'est ajouté quand
ORCHESTRATION_DISABLED vaut 1, true ou yes. Erreur : aucune sortie.
"""

import json
import os
import re
import sys

SESSION_VALIDE = re.compile(r"[A-Za-z0-9_-]{1,128}")


def option_session() -> str:
    """` --session <ID>` si stdin porte un session_id valide, sinon une chaîne vide."""
    try:
        if sys.stdin.isatty():
            return ""
        evenement = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace") or "null")
        identifiant = evenement.get("session_id") if isinstance(evenement, dict) else None
        if isinstance(identifiant, str) and SESSION_VALIDE.fullmatch(identifiant):
            return f" --session {identifiant}"
    except Exception:
        pass
    return ""


def main() -> int:
    try:
        if os.environ.get("ORCHESTRATION_DISABLED", "").strip().lower() in {"1", "true", "yes", "oui"}:
            return 0
        session = option_session()
        racine = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(racine, "context", "orchestrateur.md"), encoding="utf-8") as f:
            texte = f.read()
        texte = texte.replace("@RACINE@", racine.replace("\\", "/")).replace("@SESSION@", session)
        sys.stdout.buffer.write(texte.encode("utf-8"))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
