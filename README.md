# Everyday Work + avisos de reunión

Flujo: `sync_axis.py` (ya existente) deja las sesiones en el Gist → `build_everyday.py` las cruza con el Excel
"AXIS CLUB REGISTRO" y sube `everyday-work.json` al mismo Gist → `index.html` lo muestra por día y coach.
`notify_reuniones.py` avisa por Telegram 7 días antes de la "Próxima semana de reunión".

## Configuración (una vez)
1. `py -m pip install -r everyday/requirements.txt`
2. En `secrets.env` añade:
   - `SHEET_URL=https://drive.google.com/uc?export=download&id=<ID_DEL_EXCEL>`
     (el archivo debe estar compartido como "cualquiera con el enlace: lector"; o usa `SHEET_PATH=C:\ruta\al\excel.xlsx`).
   - `TELEGRAM_BOT_TOKEN=...` (crea un bot con @BotFather).
3. Copia `coaches.example.json` a `coaches.json` y rellena cada coach. Para el `telegram_chat_id`: cada coach
   escribe al bot y abres `https://api.telegram.org/bot<TOKEN>/getUpdates`.
4. El nombre del Excel y el de AimHarder deben coincidir (se ignoran tildes, mayúsculas y orden parcial).
   `build_everyday.py` lista los "Sin ficha" para corregirlos.

## Uso diario
```
py everyday\build_everyday.py          # tras sync_axis.py; --local para no subir al Gist
py everyday\notify_reuniones.py --dry-run
```
Dashboard: abre `everyday/index.html` (o publícalo en GitHub Pages) y pega el Gist la primera vez.
Enlace por coach: `index.html?coach=Marc`.

## Pendiente
- Automatizar (GitHub Actions / Render cron) cada mañana: sync → build → notify.
- WhatsApp: requiere Meta Cloud API o Twilio; Telegram funciona ya.
