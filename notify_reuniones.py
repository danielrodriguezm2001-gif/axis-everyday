"""Avisa por Telegram al profesional referente 7 dias antes de la 'Proxima semana de reunion'.

Ejecutar a diario. Uso: py notify_reuniones.py [--dry-run] [--antelacion 7]
"""
import argparse
from datetime import date, timedelta

import requests

from common import cargar_coaches, cargar_env, cargar_excel, coach_de


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--antelacion", type=int, default=7)
    a = ap.parse_args()
    env = cargar_env()
    coaches = cargar_coaches()
    objetivo = (date.today() + timedelta(days=a.antelacion)).isoformat()
    por_coach = {}
    for c in cargar_excel(env):
        if c["proxima"] == objetivo:
            co = coach_de(c["referente"], coaches)
            clave = (co or {}).get("nombre") or c["referente"] or "(sin referente)"
            por_coach.setdefault(clave, (co, []))[1].append(c)
    if not por_coach:
        print(f"Nadie tiene reunion el {objetivo}.")
        return
    for nombre, (co, lista) in por_coach.items():
        lineas = [f"📅 Reuniones dentro de {a.antelacion} días ({objetivo}):"]
        for c in lista:
            lineas.append(
                f"\n• {c['nombre']}\n  Objetivos: {c['objetivos'] or '-'}\n"
                f"  Lesiones: {c['lesiones'] or '-'}\n  Everyday: {', '.join(c['everyday']) or '-'}")
        msg = "\n".join(lineas)
        if a.dry_run or not co or not co.get("telegram_chat_id") or not env.get("TELEGRAM_BOT_TOKEN"):
            print(f"[no enviado] -> {nombre}" + ("" if co else " (no esta en coaches.json)") + f"\n{msg}\n")
            continue
        r = requests.post(f"https://api.telegram.org/bot{env['TELEGRAM_BOT_TOKEN']}/sendMessage",
                          json={"chat_id": co["telegram_chat_id"], "text": msg}, timeout=20)
        print(f"Enviado a {nombre}: {r.status_code}")


if __name__ == "__main__":
    main()
