#!/usr/bin/env python3
"""Hook SessionStart : ajoute au contexte les règles permanentes de l'orchestrateur.

Équivaut à un CLAUDE.md commun à tous les projets. Rien n'est ajouté quand
ORCHESTRATION_DISABLED vaut 1, true ou yes. Erreur : aucune sortie.
"""

import os
import sys


def main() -> int:
    try:
        if os.environ.get("ORCHESTRATION_DISABLED", "").strip().lower() in {"1", "true", "yes", "oui"}:
            return 0
        racine = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(racine, "context", "orchestrateur.md"), encoding="utf-8") as f:
            texte = f.read()
        texte = texte.replace("@RACINE@", racine.replace("\\", "/"))
        sys.stdout.buffer.write(texte.encode("utf-8"))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
