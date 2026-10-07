# Monitor cripto → Telegram (GitHub Actions)

Revisa cada 15 minutos BTC, ETH, POL, SOL, AVAX, EURC, HYPE, SUI, PAXG, LINK, XRP y ADA en
CoinGecko y te escribe por Telegram en dos casos:

- **🚀/🔻 Variación 24 h**: sube o cae más de ±5% en las últimas 24 h.
- **📈/📉 Precio de referencia**: se aleja ±10%, ±20%, ±30%… de la referencia.
  Al arrancar, la referencia de cada moneda es su precio de ese día. La puedes
  cambiar cuando quieras desde Telegram.

## Comandos de Telegram

| Comando | Qué hace |
|---|---|
| `/estado` | Precio, variación 24 h y distancia a la referencia de cada moneda |
| `/ref BTC 60000` | Fija la referencia de BTC en 60.000 USD |
| `/ref SOL hoy` | Toma el precio actual de SOL como referencia |
| `/ref todas hoy` | Reinicia todas las referencias al precio actual |
| `/ayuda` | Lista de comandos |

- Escribe el precio **sin separador de miles**: `60000`, `0,25` o `0.25`.
- El bot responde en la siguiente revisión (máximo ~15 min), no al instante.
- Al cambiar una referencia no se dispara alerta en ese momento; la respuesta
  ya te dice a qué % está el precio actual.
- Solo obedece mensajes de tu chat (`TELEGRAM_CHAT_ID`).

## Configuración inicial (~10 min)

1. **Crear el bot**: en Telegram abre **@BotFather** → `/newbot`. Guarda el token.
2. **chat_id**: escríbele algo a tu bot, abre
   `https://api.telegram.org/bot<TOKEN>/getUpdates` y copia el número de
   `"chat":{"id": ...}`.
3. **(Recomendado) Clave Demo gratuita de CoinGecko** (coingecko.com/en/api).
4. **Repositorio** en GitHub (público recomendado) con estos archivos,
   respetando la carpeta `.github/workflows/`.
5. **Secretos** en *Settings → Secrets and variables → Actions*:
   `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` y `COINGECKO_API_KEY` (opcional).
6. **Probar**: *Actions* → *Monitor cripto* → *Run workflow* (con "test"
   marcado). Te llega el estado de las 12 monedas. Si alguna dice "sin datos",
   su ID de CoinGecko necesita corrección en `config.json`.

## Panel gráfico (`index.html`)

Página para ver en el PC la evolución de cada moneda:
- Lista con precio, variación 24 h, mini-gráfico de 7 días y distancia a tu referencia.
- Gráfico interactivo por moneda (24 h, 7 d, 30 d, 90 d, 1 año) con zoom, cursor
  y tus líneas de referencia y ±10%.
- Próximos precios en los que el bot te alertará.
- Rendimiento comparado de todas las monedas por periodo.
- Precios en USD o COP. Se actualiza solo cada 5 min (gráfico cada 15 min) y
  con el botón Actualizar.

**Publicarlo con GitHub Pages (gratis):**
1. En el repo: *Settings → Pages → Build and deployment*.
2. *Source*: **Deploy from a branch**; *Branch*: **main** y carpeta **/ (root)** → *Save*.
3. En uno o dos minutos queda en `https://<tu-usuario>.github.io/<nombre-del-repo>/`.
   Guárdalo en favoritos.

Desde esa dirección el panel detecta solo tu repositorio y lee tus referencias.
También puedes abrir `index.html` con doble clic en tu PC; en ese caso escribe
`usuario/repo` en **Ajustes** para ver las referencias.

En **Ajustes** puedes poner tu clave Demo de CoinGecko. Se guarda solo en tu
navegador (no en el repo) y evita el límite de consultas al cambiar rápido de moneda.

**Cuota de CoinGecko.** La clave Demo da 10.000 consultas al mes. El monitor
gasta ~3.000 (una cada 15 min) y el panel se reserva 6.500, que alcanzan para
unas 10 horas diarias con el panel abierto. El panel cuenta sus consultas: si va
más rápido de lo que permite la cuota, pasa solo a actualizar cada 15 min, y si
la agota deja de actualizarse solo hasta el mes siguiente (el botón Actualizar
sigue funcionando). El conteo es por navegador; en Ajustes ves cuántas llevas.

## Ajustes (`config.json`)

| Campo | Qué hace |
|---|---|
| `coins` | Ticker → ID de CoinGecko. El ID aparece como "API ID" en la página de cada moneda. |
| `vs_currency` | `usd`, `cop`, `eur`… (las referencias se expresan en esta moneda) |
| `alert_24h.threshold_pct` | Umbral 24 h (5 = ±5%). |
| `alert_24h.escalation_step_pct` | Vuelve a avisar si el movimiento se amplía estos puntos. |
| `alert_24h.cooldown_hours` | Si sigue fuera del umbral, recordatorio cada N horas. |
| `alert_reference.step_pct` | Saltos de alerta vs. referencia (10 = avisa en ±10%, ±20%…). |
| `reset_margin_pct` | Margen para "rearmar" una alerta y evitar spam si el precio oscila justo en el umbral. |
| `reference_prices` | Opcional: fija referencias desde aquí, ej. `{"BTC": 60000}`. Tienen prioridad sobre `/ref`; bórralas para volver a manejarlas por Telegram. |

## Notas

- Las referencias y el historial de alertas se guardan en `state.json`, que el
  propio workflow actualiza en el repo. No lo borres (perderías las referencias).
- **EURC** es una stablecoin atada al euro: en USD solo se mueve con el EUR/USD,
  así que una alerta suya indica un movimiento fuerte del euro o un despegue de
  su paridad. **PAXG** sigue al oro.
- El cron de GitHub puede retrasarse unos minutos en horas de alta carga.
- **Repo privado**: 2.000 min/mes gratis; cada 15 min se pasa (~2.900). Usa `*/30`.
- GitHub desactiva los cron tras 60 días sin actividad en el repo; si llega el
  aviso, se reactiva en la pestaña *Actions*.
