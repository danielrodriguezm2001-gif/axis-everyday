"""
Sincronizacion automatica de sesiones y pagos de Axis con AimHarder.

Diferencia con la version original: en vez de reutilizar una cookie
copiada a mano del navegador (que se invalida cada pocos dias, sobre
todo si varias personas inician sesion con la misma cuenta), este
script abre un navegador invisible (headless) e inicia sesion EL
MISMO, con tu email y contrasena, cada vez que se ejecuta. Como nunca
reutiliza nada de una ejecucion a otra, no hay ninguna cookie que
pueda caducar entre ejecuciones.

Requisito extra frente a la V1: Playwright + un Chromium que se instala
una sola vez (ver requirements_v2.txt e INSTALAR.txt).

Esta es una carpeta INDEPENDIENTE de tu instalacion actual (V1). No
toca ni sustituye nada de lo que ya tienes funcionando en C:\\AxisSync.
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import requests

# ---------- configuracion ----------
DIAS_A_DESCARGAR = 7
FECHA_INICIO = "2026-04-01"
# Se descarga TODO el historial en cada sincronizacion (asi lo que se borra o
# cancela en AimHarder desaparece tambien de meses anteriores), varios dias a la vez.
DESCARGAS_EN_PARALELO = 6
VERSION_DATOS = 3

HERE = os.path.dirname(os.path.abspath(__file__))
SECRETS_PATH = os.path.join(HERE, "secrets.env")
GIST_ID_PATH = os.path.join(HERE, "gist_id.txt")


def load_secrets():
    secrets = {}
    if os.path.exists(SECRETS_PATH):
        with open(SECRETS_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                secrets[k.strip()] = v.strip().strip('"').strip("'")
    # En GitHub Actions no hay secrets.env: los datos llegan como variables
    # de entorno (GitHub no deja que un secret empiece por GITHUB_, por eso
    # el token del Gist se llama GIST_TOKEN alli).
    for k in ["AIMHARDER_EMAIL", "AIMHARDER_PASSWORD", "AIMHARDER_SUBDOMAIN"]:
        if os.environ.get(k):
            secrets[k] = os.environ[k]
    if os.environ.get("GIST_TOKEN"):
        secrets["GITHUB_TOKEN"] = os.environ["GIST_TOKEN"]
    if not secrets:
        sys.exit(
            f"No encuentro {SECRETS_PATH} ni variables de entorno.\n"
            "Copia secrets.env.example a secrets.env y rellena tus datos."
        )
    required = ["AIMHARDER_EMAIL", "AIMHARDER_PASSWORD", "AIMHARDER_SUBDOMAIN", "GITHUB_TOKEN"]
    faltan = [k for k in required if not secrets.get(k)]
    if faltan:
        sys.exit(f"Faltan valores en secrets.env: {', '.join(faltan)}")
    return secrets


def login_con_navegador(email: str, password: str, subdomain: str) -> requests.Session:
    """Abre un Chromium invisible, inicia sesion como lo haria una
    persona real, y devuelve una requests.Session ya autenticada con
    las cookies reales que genera ese login (incluida amhrdrauth,
    que el login por formulario simple no conseguia generar)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit(
            "Falta el paquete playwright. Ejecuta:\n"
            "  py -m pip install -r requirements_v2.txt\n"
            "  py -m playwright install chromium"
        )

    print("  Abriendo navegador invisible e iniciando sesion...")
    with sync_playwright() as p:
        # --no-sandbox / --disable-dev-shm-usage: necesarios dentro de un
        # contenedor (servidor en la nube); en Windows no cambian nada.
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = browser.new_page(user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ))
        page.goto("https://aimharder.com/login", wait_until="networkidle", timeout=30000)

        # La pagina bloquea el login hasta aceptar/rechazar las cookies
        # (banner real visto: "ACEPTAR TODAS" / "DENEGAR TODAS" / "CONFIGURAR
        # COOKIES", no siempre son <button> reales, asi que buscamos por texto
        # en cualquier etiqueta).
        for texto in ["ACEPTAR TODAS", "Aceptar todas", "Accept all", "Aceptar", "Accept"]:
            try:
                loc = page.get_by_text(texto, exact=False).first
                if loc.count() > 0 and loc.is_visible():
                    loc.click(timeout=3000)
                    print(f"  Aviso de cookies cerrado con el texto: {texto}")
                    page.wait_for_timeout(800)
                    break
            except Exception:
                continue

        # Diagnostico: listamos todos los <input> de la pagina para saber
        # exactamente como se llaman los campos (por si AimHarder cambia
        # el formulario en el futuro, esto ayuda a ajustar los selectores).
        try:
            inputs_info = page.eval_on_selector_all(
                "input",
                "els => els.map(e => ({name: e.name, id: e.id, type: e.type, placeholder: e.placeholder}))",
            )
            print(f"  Campos <input> encontrados en la pagina: {inputs_info}")
        except Exception as e:
            print(f"  (no se pudo listar los inputs: {e})")

        # Rellenar el formulario de login. Probamos varios selectores
        # habituales, porque no sabemos con certeza el nombre exacto del
        # campo (puede variar segun la version de AimHarder).
        candidatos_mail = [
            'input[name="mail"]', 'input[type="email"]', "#mail", "#email",
            'input[name="email"]', 'input[id*="mail" i]',
        ]
        candidatos_pw = [
            'input[name="pw"]', 'input[type="password"]', "#pw", "#password",
            'input[name="password"]',
        ]

        def rellenar(candidatos, valor, etiqueta):
            for sel in candidatos:
                try:
                    loc = page.locator(sel).first
                    if loc.count() > 0:
                        loc.fill(valor, timeout=5000)
                        print(f"  Campo {etiqueta} rellenado usando selector: {sel}")
                        return True
                except Exception:
                    continue
            return False

        ok_mail = rellenar(candidatos_mail, email, "email")
        ok_pw = rellenar(candidatos_pw, password, "contrase\u00f1a")

        if not ok_mail or not ok_pw:
            try:
                page.screenshot(path=os.path.join(HERE, "debug_login.png"), full_page=True)
                print(f"  Captura guardada en: {os.path.join(HERE, 'debug_login.png')} (\u00e1brela y mandamela si sigue fallando)")
            except Exception:
                pass
            browser.close()
            sys.exit(
                "No he podido rellenar el formulario de login automaticamente.\n"
                "Mira el listado de <input> de arriba (o la captura debug_login.png) "
                "y pasaselo a Claude para ajustar los selectores."
            )

        # Enviar el formulario. Probamos varios botones habituales; si
        # ninguno existe, pulsamos Enter en el campo de contrase\u00f1a
        # (funciona en la inmensa mayoria de formularios de login).
        enviado = False
        for sel in [
            'button[type="submit"]', 'input[type="submit"]',
            'button:has-text("Log in")', 'button:has-text("Iniciar sesi\u00f3n")',
            'button:has-text("Login")', 'button:has-text("Entrar")',
        ]:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.click(timeout=5000)
                    enviado = True
                    print(f"  Formulario enviado con el bot\u00f3n: {sel}")
                    break
            except Exception:
                continue
        if not enviado:
            print("  No encontr\u00e9 un bot\u00f3n de env\u00edo claro; pulsando Enter en el campo de contrase\u00f1a.")
            page.locator('input[type="password"]').first.press("Enter")
        page.wait_for_load_state("networkidle", timeout=30000)

        # Aviso si sigue en la pagina de login (probable error de credenciales)
        page.wait_for_timeout(1500)  # margen extra por si el redirect tarda un poco tras el submit

        if "/login" in page.url:
            error_text = ""
            try:
                error_text = page.inner_text("#loginErrors")
            except Exception:
                pass
            if not error_text:
                # buscamos cualquier texto que suene a error en toda la pagina
                try:
                    body_text = page.inner_text("body")
                    for linea in body_text.splitlines():
                        l = linea.strip().lower()
                        if l and any(p in l for p in ["incorrect", "incorrecto", "error", "invalid", "no v\u00e1lid"]):
                            error_text = linea.strip()
                            break
                except Exception:
                    pass
            try:
                shot = os.path.join(HERE, "debug_login.png")
                page.screenshot(path=shot, full_page=True)
                print(f"  Captura guardada en: {shot} (\u00e1brela y mandamela si no queda claro el motivo)")
            except Exception:
                pass
            print(f"  URL actual tras el intento de login: {page.url}")
            browser.close()
            sys.exit(
                "El login no parece haber funcionado (seguimos en /login). "
                f"Mensaje de la pagina: {error_text or '(sin mensaje visible)'}\n"
                "Revisa AIMHARDER_EMAIL / AIMHARDER_PASSWORD en secrets.env, o mira la captura debug_login.png."
            )

        # Visitar tambien el subdominio del centro para que el navegador
        # obtenga cualquier cookie adicional especifica de ese centro.
        page.goto(f"https://{subdomain}.aimharder.com/timetable", wait_until="networkidle", timeout=30000)

        cookies = page.context.cookies()
        browser.close()

    if not cookies:
        sys.exit("El login no genero ninguna cookie. Algo fue mal.")

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    })
    nombres_vistos = []
    for c in cookies:
        session.cookies.set(c["name"], c["value"], domain=c["domain"].lstrip("."), path=c.get("path", "/"))
        nombres_vistos.append(f"{c['name']} ({c['domain']})")
    print(f"  Cookies obtenidas del login real: {', '.join(nombres_vistos)}")

    tiene_auth = any(c["name"] == "amhrdrauth" for c in cookies)
    print(f"  \u00bfSe obtuvo la cookie amhrdrauth? {'s\u00ed' if tiene_auth else 'NO (puede que falle mas adelante)'}")

    return session


def fetch_day(session: requests.Session, subdomain: str, day_iso: str) -> dict:
    day_compact = day_iso.replace("-", "")
    url = f"https://{subdomain}.aimharder.com/api/coachBookings"
    combinadas = {}
    for appts in (1, 0):
        resp = session.get(url, params={"appointments": appts, "day": day_compact, "_": "0"}, timeout=20)
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError:
            print(f"  aviso: {day_iso} (appointments={appts}) no devolvio JSON valido.")
            continue
        for b in data.get("bookings", []) or []:
            bid = b.get("id")
            if bid is None or bid in combinadas:
                continue
            b = quitar_cancelaciones(b)
            if b is not None:
                combinadas[bid] = b
    return {"bookings": list(combinadas.values())}


def quitar_cancelaciones(b: dict):
    """AimHarder sigue devolviendo las reservas canceladas (con cancelDay)
    aunque ya no aparezcan en su calendario. Quitamos esos clientes y, si es
    una cita (eventType 2) que se queda sin ningun cliente, la cita entera.
    Las clases de grupo se conservan aunque se queden vacias, como en AimHarder."""
    athletes = b.get("athletes") or []
    activos = [a for a in athletes if not a.get("cancelDay")]
    if b.get("eventType") == 2 and athletes and not activos:
        return None
    if len(activos) != len(athletes):
        b = dict(b, athletes=activos)
    return b


CAMPOS_AFORO_CANDIDATOS = [
    "limit", "classLimit", "totalSpaces", "spaces", "capacity",
    "numSpaces", "max", "maxAthletes", "aforo", "limitAthletes",
    "totalLimit", "boxLimit",
]

_aforo_debug_mostrado = {"n": 0}


def _detectar_aforo(b: dict):
    """Intenta encontrar el campo de aforo (plazas totales) de una clase
    probando varios nombres de campo habituales en APIs de este estilo.
    No sabemos aun con certeza cual usa AimHarder, asi que devolvemos el
    primero que aparezca y ademas mostramos un aviso en consola con el
    contenido completo de la reserva (solo las primeras 5 veces) para
    poder confirmar o corregir el nombre exacto del campo."""
    encontrado = None
    campo_usado = None
    for campo in CAMPOS_AFORO_CANDIDATOS:
        v = b.get(campo)
        if v is not None and v != "":
            try:
                encontrado = int(v)
                campo_usado = campo
                break
            except (TypeError, ValueError):
                continue

    if _aforo_debug_mostrado["n"] < 5:
        _aforo_debug_mostrado["n"] += 1
        print(
            f"  [debug aforo #{_aforo_debug_mostrado['n']}] reserva completa "
            f"para comprobar el campo de aforo: {json.dumps(b, ensure_ascii=False)}"
        )
        print(
            f"  [debug aforo #{_aforo_debug_mostrado['n']}] campo detectado: "
            f"{campo_usado!r} = {encontrado!r} (si esto no coincide con el aforo "
            "real que ves en AimHarder, pasame estas 2 lineas de debug para "
            "corregir el nombre del campo)"
        )

    return encontrado


def slim_booking(b: dict) -> dict:
    athletes = b.get("athletes") or []
    nombres = []
    for a in athletes:
        n = (a.get("realName") or a.get("name") or "").strip()
        if n:
            nombres.append({"name": n})
    return {
        "id": b.get("id"), "time": b.get("time"), "timeid": b.get("timeid"),
        "startTime": b.get("startTime"), "classLength": b.get("classLength"),
        "className": b.get("className"), "coachName": b.get("coachName"),
        "salaname": b.get("salaname"), "athletes": nombres,
        "limit": _detectar_aforo(b),
    }


def fetch_payments(session: requests.Session, subdomain: str, fecha_inicio: str) -> dict:
    stamp = str(int(time.time() * 1000))

    def to_amount(p):
        raw = p.get("amountc")
        if raw is None:
            raw = p.get("amount", "0")
        raw = str(raw).replace("\u20ac", "").replace(",", ".").strip()
        try:
            return round(float(raw), 2)
        except ValueError:
            return 0.0

    def slim_pending(p):
        return {"id": p.get("id"), "name": p.get("name"), "amount": to_amount(p),
                "concept": p.get("concept"), "date": p.get("sinceYYYYMMDD"), "creator": p.get("creator")}

    def forma_pago(p):
        # Misma logica que la pantalla de pagos finalizados de AimHarder.
        if p.get("gcState") in (1, 2):
            return "domiciliado"
        return {1: "efectivo", 2: "transferencia"}.get(p.get("cash"), "tarjeta")

    def slim_paid(p):
        return {"id": p.get("id"), "name": p.get("name"), "amount": to_amount(p),
                "concept": p.get("concept"), "date": p.get("payDate"), "creator": p.get("creator"),
                "method": forma_pago(p)}

    pending, paid = [], []
    fallo_pending = fallo_paid = False
    try:
        r = session.get(
            f"https://{subdomain}.aimharder.com/api/pendingPayments",
            params={"from": fecha_inicio, "to": "", "type": -1, "clientId": "", "concept": "", "_": stamp},
            timeout=30,
        )
        r.raise_for_status()
        pending = [slim_pending(p) for p in (r.json().get("pending") or [])]
    except Exception as e:
        fallo_pending = True
        print(f"  aviso: no se pudieron descargar pagos pendientes ({e})")

    try:
        r = session.get(
            f"https://{subdomain}.aimharder.com/api/finishedPayments",
            params={
                "from": "", "to": "", "fromPay": fecha_inicio, "toPay": "", "type": -1, "state": -1,
                "clientId": "", "seller": "", "sellermark": "", "saleType": "", "saleProduct": "",
                "saleMembership": "", "concept": "", "_": stamp,
            },
            timeout=30,
        )
        r.raise_for_status()
        paid = [slim_paid(p) for p in (r.json().get("paid") or [])]
    except Exception as e:
        fallo_paid = True
        print(f"  aviso: no se pudieron descargar pagos finalizados ({e})")

    if fallo_pending or fallo_paid:
        print(
            "  IMPORTANTE: la descarga de pagos ha fallado. NO se subiran pagos\n"
            "  en esta ejecucion, para no borrar los datos buenos que ya hubiera\n"
            "  guardados. Corrige el problema y vuelve a ejecutar."
        )
        return None

    print(f"  Pagos: {len(pending)} pendiente(s), {len(paid)} finalizado(s)")
    return {"pending": pending, "paid": paid}


def read_gist_id():
    """ID del Gist: variable de entorno GIST_ID (GitHub Actions) o gist_id.txt."""
    if os.environ.get("GIST_ID"):
        return os.environ["GIST_ID"].strip()
    if os.path.exists(GIST_ID_PATH):
        with open(GIST_ID_PATH, "r", encoding="utf-8") as f:
            return f.read().strip() or None
    return None


def download_existing_payments(github_token: str):
    """Devuelve los pagos ya guardados en el Gist, o None si no hay."""
    gist_id = read_gist_id()
    if not gist_id:
        return None
    try:
        r = requests.get(
            f"https://api.github.com/gists/{gist_id}",
            headers={"Authorization": f"Bearer {github_token}", "Accept": "application/vnd.github+json"},
            timeout=20,
        )
        if r.status_code != 200:
            return None
        content = r.json()["files"]["axis-sessions.json"]["content"]
        return json.loads(content).get("payments")
    except Exception:
        return None


def build_payload(subdomain: str, session: requests.Session) -> dict:
    today = date.today()
    inicio = date.fromisoformat(FECHA_INICIO)
    fin = today + timedelta(days=DIAS_A_DESCARGAR - 1)
    a_descargar = [(inicio + timedelta(days=i)).isoformat() for i in range((fin - inicio).days + 1)]
    print(f"  Dias a descargar: {len(a_descargar)} (todo el historial, {DESCARGAS_EN_PARALELO} a la vez)")

    # Si falla cualquier dia, la excepcion corta la ejecucion y NO se sube
    # nada: mejor conservar el Gist anterior que subir un historial a medias.
    with ThreadPoolExecutor(max_workers=DESCARGAS_EN_PARALELO) as pool:
        resultados = list(pool.map(lambda iso: fetch_day(session, subdomain, iso), a_descargar))

    days = {}
    for iso, data in zip(a_descargar, resultados):
        days[iso] = [slim_booking(b) for b in data.get("bookings", [])]
    total = sum(len(v) for v in days.values())
    print(f"  Sesiones descargadas: {total}")

    if total == 0:
        print(
            "\nAVISO: 0 sesiones en los dias consultados. Si sabes que SI hay "
            "sesiones, puede que el login automatico no se haya autenticado "
            "correctamente (revisa el aviso de 'amhrdrauth' de mas arriba)."
        )

    return {
        "generated_at": today.isoformat(),
        # Momento exacto (UTC) de esta sincronizacion: el panel lo usa para
        # saber si el Gist ya trae los datos de la ejecucion que lanzo.
        "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_version": VERSION_DATOS,
        "days": days,
    }


def upload_to_gist(github_token: str, payload: dict) -> str:
    headers = {"Authorization": f"Bearer {github_token}", "Accept": "application/vnd.github+json"}
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    files = {"axis-sessions.json": {"content": content}}

    gist_id = read_gist_id()

    if gist_id:
        r = requests.patch(f"https://api.github.com/gists/{gist_id}", headers=headers, json={"files": files}, timeout=20)
        if r.status_code == 404:
            gist_id = None
        else:
            r.raise_for_status()

    if not gist_id:
        r = requests.post(
            "https://api.github.com/gists", headers=headers,
            json={"description": "Axis - sesiones sincronizadas (uso interno del panel)", "public": False, "files": files},
            timeout=20,
        )
        r.raise_for_status()
        gist_id = r.json()["id"]
        with open(GIST_ID_PATH, "w", encoding="utf-8") as f:
            f.write(gist_id)

    data = r.json()
    api_url = f"https://api.github.com/gists/{gist_id}"
    return api_url


def main():
    secrets = load_secrets()
    print("Iniciando sesion automatica en AimHarder (navegador invisible)...")
    session = login_con_navegador(secrets["AIMHARDER_EMAIL"], secrets["AIMHARDER_PASSWORD"], secrets["AIMHARDER_SUBDOMAIN"])
    print("Sesion iniciada.")
    print("Descargando sesiones...")
    payload = build_payload(secrets["AIMHARDER_SUBDOMAIN"], session)
    print("Descargando pagos...")
    pagos = fetch_payments(session, secrets["AIMHARDER_SUBDOMAIN"], FECHA_INICIO)
    if pagos is not None:
        payload["payments"] = pagos
    else:
        # Conservamos los pagos que ya hubiera en el Gist en vez de borrarlos.
        anteriores = download_existing_payments(secrets["GITHUB_TOKEN"])
        if anteriores is not None:
            payload["payments"] = anteriores
            print("  Se conservan los pagos de la sincronizacion anterior.")
    print("Subiendo a GitHub Gist...")
    api_url = upload_to_gist(secrets["GITHUB_TOKEN"], payload)
    print("\nListo. Pega esta URL UNA VEZ en el panel, campo 'URL de sincronizacion':")
    print(api_url)


if __name__ == "__main__":
    main()
