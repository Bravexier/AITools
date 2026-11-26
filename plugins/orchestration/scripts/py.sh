#!/bin/sh
# Lance le script Python donné avec le premier interpréteur Python 3.8+ qui
# fonctionne vraiment : python3, python, puis py (lanceur Windows). Sous
# Windows, « python3 » peut n'être qu'un raccourci du Store qui échoue ; il est
# alors écarté. Sans Python, sort avec 127 (jamais 2 : un hook ne bloque pas).
for candidat in python3 python py; do
  if command -v "$candidat" >/dev/null 2>&1 \
    && "$candidat" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
    exec "$candidat" "$@"
  fi
done
echo "orchestration : Python 3.8+ introuvable (python3, python ou py)." >&2
exit 127
