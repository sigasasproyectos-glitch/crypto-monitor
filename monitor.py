#!/usr/bin/env python3
"""
Monitor de criptomonedas con alertas por Telegram.

Dos tipos de alerta:
1) Variación 24 h: avisa cuando una moneda sube o cae más de ±5% en 24 h.
   No repite salvo que el movimiento se amplíe otros 5 puntos, cambie de
   dirección o pasen 12 h.
2) Precio de referencia: avisa cada vez que el precio se aleja ±10%, ±20%,
   ±30%... de su referencia. La referencia es, por defecto, el precio del día
   en que el monitor la registra; se puede cambiar por Telegram (/ref) o
   fijar en config.json.

Comandos de Telegram (se procesan en cada ejecución, cada ~15 min):
  /ref BTC 60000     fija la referencia de BTC en 60000
  /ref BTC hoy       toma el precio actual como referencia
  /ref todas hoy     reinicia todas las referencias al precio actual
  /estado            precios, variación 24 h y distancia a la referencia
  /ayuda             lista de comandos
"""
import html
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).parent
CONFIG_PATH = BASE / "config.json"
STATE_PATH = BASE / "state.json"
TZ = timezone(timedelta(hours=-5))  # Colombia
MESES = ["ene", "feb", "mar", "abr", "may", "jun",
         "jul", "ago", "sep", "oct", "nov", "dic"]

HELP = (
    "<b>Comandos</b>\n"
    "/estado – precios y distancia a la referencia\n"
    "/ref BTC 60000 – fija la referencia de BTC\n"
    "/ref BTC hoy – usa el precio actual como referencia\n"
    "/ref todas hoy – reinicia todas al precio actual\n\n"
    "Escribe el precio sin separador de miles (60000 o 0,25).\n"
    "Los comandos se procesan en la siguiente revisión (máx. ~15 min)."
)


# ---------------------------------------------------------------- utilidades
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def http_json(req, retries=3):
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                time.sleep(20 * (attempt + 1))
                continue
            body = e.read().decode(errors="ignore")[:300]
            raise RuntimeError(f"HTTP {e.code}: {body}") from e
        except urllib.error.URLError:
            if attempt < retries - 1:
                time.sleep(5)
                continue
            raise


def today_iso():
    return datetime.now(TZ).strftime("%Y-%m-%d")


def fmt_date(iso):
    try:
        d = datetime.strptime(iso, "%Y-%m-%d")
        return f"{d.day:02d}-{MESES[d.month - 1]}-{d.year}"
    except (TypeError, ValueError):
        return "?"


def fmt_price(p):
    if p >= 1:
        return f"{p:,.2f}"
    return f"{p:.6g}"


def parse_price(s):
    s = s.strip().replace(" ", "")
    if "," in s and "." in s:          # el último separador es el decimal
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    else:
        s = s.replace(",", ".")
    v = float(s)
    if v <= 0:
        raise ValueError
    return v


# ---------------------------------------------------------------- APIs
def fetch_prices(ids, vs, api_key):
    params = urllib.parse.urlencode({
        "ids": ",".join(ids),
        "vs_currencies": vs,
        "include_24hr_change": "true",
    })
    headers = {"accept": "application/json", "User-Agent": "crypto-monitor"}
    if api_key:
        headers["x-cg-demo-api-key"] = api_key
    req = urllib.request.Request(
        f"https://api.coingecko.com/api/v3/simple/price?{params}", headers=headers)
    return http_json(req)


def tg_call(token, method, params):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}", data=data)
    resp = http_json(req)
    if not resp.get("ok"):
        raise RuntimeError(f"Telegram {method}: {resp}")
    return resp["result"]


def send_telegram(token, chat_id, text):
    tg_call(token, "sendMessage", {
        "chat_id": chat_id, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": "true"})


def get_commands(token, chat_id, offset):
    updates = tg_call(token, "getUpdates", {
        "offset": offset, "timeout": 0,
        "allowed_updates": json.dumps(["message"])})
    cmds = []
    for u in updates:
        offset = max(offset, u["update_id"] + 1)
        msg = u.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != str(chat_id):
            continue  # ignora mensajes de otros chats
        text = (msg.get("text") or "").strip()
        if text.startswith("/"):
            cmds.append(text)
    return cmds, offset


# ---------------------------------------------------------------- lógica
class Monitor:
    def __init__(self, cfg, state, prices):
        self.vs = cfg.get("vs_currency", "usd").lower()
        self.unit = self.vs.upper()
        self.tickers = {t.upper(): cid for t, cid in cfg["coins"].items()}
        a24 = cfg.get("alert_24h", {})
        self.th24 = float(a24.get("threshold_pct", 5))
        self.step24 = float(a24.get("escalation_step_pct", 5))
        self.cool24 = float(a24.get("cooldown_hours", 12))
        self.margin24 = float(a24.get("reset_margin_pct", 1))
        ar = cfg.get("alert_reference", {})
        self.step_ref = float(ar.get("step_pct", 10))
        self.margin_ref = float(ar.get("reset_margin_pct", 1))
        self.cfg_refs = {k.upper(): v for k, v in cfg.get("reference_prices", {}).items()
                         if not k.startswith("_") and v not in (None, "")}
        self.state = state
        self.state.setdefault("coins", {})
        self.prices = prices  # ticker -> (price, change24)

    def cs(self, t):
        return self.state["coins"].setdefault(t, {})

    def ref_pct(self, t):
        price = self.prices[t][0]
        ref = self.cs(t)["ref"]["price"]
        return (price / ref - 1) * 100

    def set_ref(self, t, ref_price, source):
        cs = self.cs(t)
        cs["ref"] = {"price": ref_price, "source": source, "date": today_iso()}
        cs["ref_band"] = int(self.ref_pct(t) / self.step_ref)  # sin alerta inmediata

    def ensure_ref(self, t):
        cs = self.cs(t)
        cfgv = self.cfg_refs.get(t)
        cur = cs.get("ref", {})
        if cfgv is not None:
            if cur.get("source") != "config" or cur.get("price") != float(cfgv):
                self.set_ref(t, float(cfgv), "config")
        elif not cur or cur.get("source") == "config":
            self.set_ref(t, self.prices[t][0], "auto")

    # ---- comandos
    def handle(self, text):
        parts = text.split()
        cmd = parts[0].lower().split("@")[0]
        if cmd in ("/estado", "/refs", "/status"):
            return self.status()
        if cmd in ("/ref", "/referencia"):
            return self.cmd_ref(parts[1:])
        return HELP

    def cmd_ref(self, args):
        if len(args) != 2:
            return "Uso: /ref BTC 60000  ·  /ref BTC hoy  ·  /ref todas hoy"
        target, value = args[0].upper(), args[1].lower()
        if target in ("TODAS", "TODOS", "ALL"):
            targets = list(self.prices)
        elif target in self.tickers:
            if target not in self.prices:
                return f"No hay precio de {target} en este momento; intenta luego."
            targets = [target]
        else:
            return (f"No reconozco «{html.escape(args[0])}». "
                    f"Monedas: {', '.join(self.tickers)}")
        is_today = value in ("hoy", "actual", "ahora")
        if not is_today:
            if len(targets) > 1:
                return "Para todas solo puedo usar: /ref todas hoy"
            try:
                manual = parse_price(value)
            except ValueError:
                return f"«{html.escape(args[1])}» no es un precio válido."
        lines, blocked = [], []
        for t in targets:
            if t in self.cfg_refs:
                blocked.append(t)
                continue
            if is_today:
                self.set_ref(t, self.prices[t][0], "manual")
            else:
                self.set_ref(t, manual, "manual")
            lines.append(f"<b>{t}</b>: ref {fmt_price(self.cs(t)['ref']['price'])} {self.unit} "
                         f"(precio actual {self.ref_pct(t):+.2f}%)")
        out = "📌 Referencia actualizada\n" + "\n".join(lines) if lines else ""
        if blocked:
            out += ("\n\n" if out else "") + (
                f"⚠️ {', '.join(blocked)} tiene la referencia fijada en config.json; "
                "cámbiala o bórrala ahí.")
        return out

    def status(self):
        rows = []
        for t in self.tickers:
            if t not in self.prices:
                rows.append(f"<b>{t}</b>: sin datos")
                continue
            price, ch = self.prices[t]
            ref = self.cs(t)["ref"]
            rows.append(
                f"<b>{t}</b>  {fmt_price(price)} {self.unit}\n"
                f"   24 h {ch:+.2f}%  ·  vs ref {self.ref_pct(t):+.2f}% "
                f"({fmt_price(ref['price'])}, {fmt_date(ref['date'])})")
        return "📊 <b>Estado</b>\n\n" + "\n".join(rows)

    # ---- alertas
    def check_24h(self, t, now):
        price, ch = self.prices[t]
        cs = self.cs(t)
        prev = cs.get("alert24")
        if abs(ch) >= self.th24:
            d = "up" if ch > 0 else "down"
            fire = (not prev or prev["direction"] != d
                    or abs(ch) >= abs(prev["change"]) + self.step24
                    or (now - prev["ts"]) / 3600 >= self.cool24)
            if fire:
                cs["alert24"] = {"direction": d, "change": round(ch, 2), "ts": now}
                icon, verb = ("🚀", "sube") if d == "up" else ("🔻", "cae")
                return (f"{icon} <b>{t}</b> {verb} <b>{ch:+.2f}%</b> en 24 h\n"
                        f"Precio: {fmt_price(price)} {self.unit}")
        elif abs(ch) < self.th24 - self.margin24:
            cs.pop("alert24", None)
        return None

    def check_ref(self, t):
        cs = self.cs(t)
        pct = self.ref_pct(t)
        band = int(pct / self.step_ref)
        last = cs.get("ref_band", 0)
        # baja de banda (con margen) en silencio para poder volver a avisar
        if last and abs(pct) < abs(last) * self.step_ref - self.margin_ref:
            cs["ref_band"] = last = band
        if band and (abs(band) > abs(last) or (last and (band > 0) != (last > 0))):
            cs["ref_band"] = band
            ref = cs["ref"]
            icon = "📈" if band > 0 else "📉"
            return (f"{icon} <b>{t}</b> está <b>{pct:+.2f}%</b> respecto a su referencia\n"
                    f"Precio: {fmt_price(self.prices[t][0])} {self.unit} · "
                    f"Ref: {fmt_price(ref['price'])} ({fmt_date(ref['date'])})")
        return None


def main():
    cfg = load_json(CONFIG_PATH, None)
    if not cfg:
        sys.exit("No se encontró o no es válido config.json")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        sys.exit("Faltan los secretos TELEGRAM_BOT_TOKEN y/o TELEGRAM_CHAT_ID")
    api_key = os.environ.get("COINGECKO_API_KEY", "").strip()
    test_mode = os.environ.get("TEST_MODE", "0") in ("1", "true", "True")

    state = load_json(STATE_PATH, {})
    if "coins" not in state:            # formato antiguo o vacío
        state = {"telegram_offset": state.get("telegram_offset", 0)}
    original = json.dumps(state, sort_keys=True)
    vs = cfg.get("vs_currency", "usd").lower()

    raw = fetch_prices(list(cfg["coins"].values()), vs, api_key)
    prices = {}
    for t, cid in cfg["coins"].items():
        info = raw.get(cid) or {}
        ch = info.get(f"{vs}_24h_change")
        if vs in info and ch is not None:
            prices[t.upper()] = (float(info[vs]), float(ch))
        else:
            print(f"⚠️  {t} ({cid}): sin datos (¿ID de CoinGecko correcto?)")

    m = Monitor(cfg, state, prices)
    for t in prices:
        m.ensure_ref(t)

    # comandos de Telegram
    cmds, state["telegram_offset"] = get_commands(
        token, chat_id, state.get("telegram_offset", 0))
    for c in cmds:
        print(f"Comando: {c}")
        reply = m.handle(c)
        if reply:
            send_telegram(token, chat_id, reply)

    # alertas
    now = int(time.time())
    alerts = []
    for t in m.tickers:
        if t not in prices:
            continue
        p, ch = prices[t]
        print(f"{t:>5}: {fmt_price(p):>14} {m.unit}  24h {ch:+6.2f}%  ref {m.ref_pct(t):+7.2f}%")
        for a in (m.check_24h(t, now), m.check_ref(t)):
            if a:
                alerts.append(a)

    if test_mode:
        send_telegram(token, chat_id, "✅ <b>Monitor funcionando</b>\n\n" + m.status()
                      + "\n\nEscribe /ayuda para ver los comandos.")
    if alerts:
        send_telegram(token, chat_id, "\n\n".join(alerts))
    print(f"{len(alerts)} alerta(s) enviada(s).")

    if json.dumps(state, sort_keys=True) != original:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, sort_keys=True, ensure_ascii=False)
            f.write("\n")


if __name__ == "__main__":
    main()
