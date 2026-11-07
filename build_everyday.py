"""Cruza las sesiones de AimHarder con el Excel y publica everyday-work.json en el Gist.

Uso: py build_everyday.py [--live] [--dia AAAA-MM-DD] [--dias N] [--local]
  --live    pide las sesiones directamente a AimHarder (solo los proximos dias, rapido);
            sin --live las lee del Gist que deja sync_axis.py.
  --dias N  genera hoy + N-1 dias siguientes (por defecto 14).
  --local   no sube al Gist (solo escribe everyday-work.json).
"""
import argparse
import contextlib
import io
import json
import os
import sys
from datetime import date, datetime, timedelta

import requests

from common import HERE, buscar_cliente, cargar_coaches, cargar_env, cargar_excel, coach_de, gist_y_token, sesiones_del_gist

sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))


def sesiones_en_vivo(env, ini, n):
    """Inicia sesion en AimHarder y descarga solo los dias necesarios."""
    import sync_axis
    for k in ("AIMHARDER_EMAIL", "AIMHARDER_PASSWORD", "AIMHARDER_SUBDOMAIN"):
        if env.get(k):
            os.environ[k] = env[k]
    if env.get("GIST_TOKEN") or env.get("GITHUB_TOKEN"):
        os.environ.setdefault("GIST_TOKEN", env.get("GIST_TOKEN") or env["GITHUB_TOKEN"])
    # sync_axis imprime datos de clientes (telefonos...) en sus mensajes de depuracion; los logs de
    # GitHub Actions de un repositorio publico los ve cualquiera, asi que se silencia su salida.
    with contextlib.redirect_stdout(io.StringIO()):
        sec = sync_axis.load_secrets()
        session = sync_axis.login_con_navegador(sec["AIMHARDER_EMAIL"], sec["AIMHARDER_PASSWORD"], sec["AIMHARDER_SUBDOMAIN"])
        days = {}
        for i in range(n):
            iso = (ini + timedelta(days=i)).isoformat()
            # Si falla un dia, la excepcion corta: mejor dejar los datos anteriores que subir a medias.
            d = sync_axis.fetch_day(session, sec["AIMHARDER_SUBDOMAIN"], iso)
            days[iso] = [sync_axis.slim_booking(b) for b in d.get("bookings", [])]
    return {"days": days}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--dia")
    ap.add_argument("--dias", type=int, default=14)
    ap.add_argument("--local", action="store_true")
    a = ap.parse_args()

    env = cargar_env()
    ini = date.fromisoformat(a.dia) if a.dia else date.today()
    clientes = cargar_excel(env)
    coaches = cargar_coaches()
    gid, tok = gist_y_token(env)
    if a.live:
        data = sesiones_en_vivo(env, ini, a.dias)
    else:
        _, _, data = sesiones_del_gist(env)
    print(f"Excel: {len(clientes)} clientes. Sesiones: {len(data.get('days', {}))} dias.")

    dias, sin_ficha = {}, set()
    for i in range(a.dias):
        iso = (ini + timedelta(days=i)).isoformat()
        sesiones = []
        for b in sorted(data.get("days", {}).get(iso, []), key=lambda b: b.get("time") or ""):
            personas = []
            for at in b.get("athletes", []):
                c = buscar_cliente(at["name"], clientes)
                if not c:
                    sin_ficha.add(at["name"])
                # Pantalla publica en el club: solo nombre y ejercicios (nada de salud ni objetivos).
                personas.append({"aim": at["name"], "ficha": {"nombre": c["nombre"], "everyday": c["everyday"]} if c else None})
            co = coach_de(b.get("coachName") or "", coaches)
            sesiones.append({
                "hora": b.get("time"), "clase": b.get("className"), "sala": b.get("salaname"),
                "coach": (co or {}).get("nombre") or b.get("coachName"), "personas": personas,
            })
        dias[iso] = sesiones

    out = {"generado": datetime.now().astimezone().isoformat(timespec="seconds"), "dias": dias}
    ruta = os.path.join(HERE, "everyday-work.json")
    json.dump(out, open(ruta, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"Escrito {ruta}")
    if sin_ficha:
        # En CI (repositorio publico) los logs los ve cualquiera: sin nombres de clientes.
        detalle = f"{len(sin_ficha)} personas" if os.environ.get("CI") else ", ".join(sorted(sin_ficha))
        print("Sin ficha en el Excel (revisar nombres):", detalle)

    if not a.local:
        if not (tok and gid):
            raise SystemExit("Faltan GIST_TOKEN y GIST_ID para subir al Gist.")
        r = requests.patch(f"https://api.github.com/gists/{gid}",
                           headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"},
                           json={"files": {"everyday-work.json": {"content": json.dumps(out, ensure_ascii=False)}}}, timeout=30)
        r.raise_for_status()
        print("Subido al Gist como everyday-work.json")


if __name__ == "__main__":
    main()
