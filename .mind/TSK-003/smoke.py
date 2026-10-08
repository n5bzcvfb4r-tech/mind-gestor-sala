"""Smoke de arranque: carga Django, resuelve las rutas del contrato y sale 0/1."""
import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.urls import reverse  # noqa: E402
from django.urls.resolvers import get_resolver  # noqa: E402

resolver = get_resolver()
rutas = sorted(str(p.pattern) for p in resolver.url_patterns)
print("composition root cargado:", resolver.urlconf_name)
fallos = []
for nombre in sys.argv[1:] or ["core_security:auth-sessions"]:
    try:
        print(f"  {nombre} -> {reverse(nombre)}")
    except Exception as exc:  # noqa: BLE001
        fallos.append(f"{nombre}: {exc}")
for f in fallos:
    print("FALLO:", f)
sys.exit(1 if fallos else 0)
