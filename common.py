"""Utilidades compartidas: leer el Excel de registro, cruzar nombres y cargar coaches."""
import io
import json
import os
import re
import unicodedata
from datetime import date, datetime

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def cargar_env():
    """Mezcla secrets.env (local) con variables de entorno (nube)."""
    env = {}
    p = os.path.join(ROOT, "secrets.env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    env.update({k: v for k, v in os.environ.items() if v})
    return env


def norm(s):
    s = unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode()
    s = re.sub(r"\(.*?\)", " ", s)  # quita "(EJEMPLO)"
    return re.sub(r"[^a-z0-9 ]", " ", s.lower()).split()


def parse_fecha(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    m = re.match(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})", str(v or ""))
    return date(int(m[3]), int(m[2]), int(m[1])) if m else None


def dividir_ejercicios(texto):
    """Separa por / , ; o salto de linea, pero no dentro de parentesis."""
    partes, actual, nivel = [], "", 0
    for ch in str(texto or ""):
        nivel += (ch == "(") - (ch == ")" and nivel > 0)
        if nivel == 0 and ch in "/,;\n":
            partes.append(actual)
            actual = ""
        else:
            actual += ch
    partes.append(actual)
    return [p.strip(" -") for p in partes if p.strip(" -")]


def cargar_excel(env):
    """Descarga (SHEET_URL) o abre (SHEET_PATH) el Excel y devuelve una lista de clientes."""
    import openpyxl
    if env.get("SHEET_PATH"):
        wb = openpyxl.load_workbook(env["SHEET_PATH"], data_only=True)
    elif env.get("GOOGLE_SA_JSON") and env.get("SHEET_FILE_ID"):
        # Excel privado: cuenta de servicio de Google con acceso solo a ese archivo.
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession
        cred = service_account.Credentials.from_service_account_info(
            json.loads(env["GOOGLE_SA_JSON"]), scopes=["https://www.googleapis.com/auth/drive.readonly"])
        r = AuthorizedSession(cred).get(
            f"https://www.googleapis.com/drive/v3/files/{env['SHEET_FILE_ID']}?alt=media", timeout=60)
        r.raise_for_status()
        wb = openpyxl.load_workbook(io.BytesIO(r.content), data_only=True)
    elif env.get("SHEET_URL"):
        r = requests.get(env["SHEET_URL"], timeout=60)
        r.raise_for_status()
        if r.content[:2] != b"PK":
            raise SystemExit("SHEET_URL no devolvio un .xlsx: comparte el archivo con 'cualquiera con el enlace' (lector).")
        wb = openpyxl.load_workbook(io.BytesIO(r.content), data_only=True)
    else:
        raise SystemExit("Define SHEET_URL o SHEET_PATH en secrets.env (ver everyday/README.md).")
    ws = next((w for w in wb.worksheets if "valoraci" in w.title.lower()), wb.worksheets[0])
    filas = list(ws.iter_rows(values_only=True))
    h = next(i for i, f in enumerate(filas) if f and str(f[0] or "").strip().lower() == "nombre")
    cab = [str(c or "").strip().lower() for c in filas[h]]

    def col(*claves):
        for i, c in enumerate(cab):
            if all(k in c for k in claves):
                return i
        return None

    ix = {
        "nombre": 0, "objetivos": col("objetivos actuales"),
        "mov": col("objetivos de movimiento"),
        "lesiones": col("lesiones/molestias actuales"), "patologias": col("patolog"),
        "everyday": col("every"), "proxima": col("xima semana"),
        "referente": col("referente"),
    }
    out = []
    for f in filas[h + 1:]:
        nombre = str(f[0] or "").strip()
        if not norm(nombre) or norm(nombre) == ["profesional"]:
            continue

        def g(k):
            return f[ix[k]] if ix[k] is not None and ix[k] < len(f) else None

        prox = parse_fecha(g("proxima"))
        out.append({
            "nombre": re.sub(r"\s*\(.*?\)\s*", "", nombre).strip(),
            "ejemplo": "ejemplo" in nombre.lower(),
            "objetivos": str(g("objetivos") or "").strip(),
            "mov": str(g("mov") or "").strip(),
            "lesiones": str(g("lesiones") or "").strip(),
            "patologias": str(g("patologias") or "").strip(),
            "everyday": dividir_ejercicios(g("everyday")),
            "proxima": prox.isoformat() if prox else None,
            "referente": re.sub(r"(?i)^profesional\s*", "", str(g("referente") or "")).strip(),
        })
    return [c for c in out if not c["ejemplo"]]


def buscar_cliente(nombre_aim, clientes):
    """Cruza un nombre de AimHarder con el Excel: exacto y, si no, por palabras contenidas."""
    a = norm(nombre_aim)
    if not a:
        return None
    sa = set(a)
    mejor = None
    for c in clientes:
        n = norm(c["nombre"])
        if n == a:
            return c
        sn = set(n)
        if sn and (sn <= sa or sa <= sn) and min(len(sn), len(sa)) >= 2:
            mejor = mejor or c
    return mejor


def cargar_coaches():
    p = os.path.join(HERE, "coaches.json")
    if not os.path.exists(p):
        return []
    return json.load(open(p, encoding="utf-8")).get("coaches", [])


def coach_de(texto, coaches):
    t = set(norm(texto))
    for c in coaches:
        for al in c.get("alias", [c["nombre"]]):
            s = set(norm(al))
            if s and s <= t:
                return c
    return None


def gist_y_token(env):
    gid = env.get("GIST_ID")
    if not gid:
        p = os.path.join(ROOT, "gist_id.txt")
        gid = open(p).read().strip() if os.path.exists(p) else ""
    return gid, env.get("GIST_TOKEN") or env.get("GITHUB_TOKEN")


def sesiones_del_gist(env):
    gid, tok = gist_y_token(env)
    h = {"Accept": "application/vnd.github+json"}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    r = requests.get(f"https://api.github.com/gists/{gid}", headers=h, timeout=30)
    r.raise_for_status()
    return gid, tok, json.loads(r.json()["files"]["axis-sessions.json"]["content"])
