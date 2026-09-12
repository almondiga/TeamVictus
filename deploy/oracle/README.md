# Despliegue en Oracle Cloud Always Free (0 €/mes, 24/7)

Guía completa para tener el bot **TeamVictus** corriendo 24/7 gratis para siempre.
Duración total: ~30 min la primera vez.

> **Resumen de lo que vas a crear:**
> - 1 VM AMD **VM.Standard.E2.1.Micro** (1 GB RAM) — siempre gratis, sin caducidad.
> - El bot como **servicio systemd**: arranca solo al encender la VM y se auto-reinicia si se cae.
> - (Opcional) UptimeRobot vigilando el endpoint `/health`.

---

## 1. Crear la cuenta Oracle Cloud (una sola vez)

1. Entra en **https://signup.cloud.oracle.com** y rellena el formulario (datos reales).
2. Te pedirán **tarjeta** solo para **verificar la identidad**: hacen una retención de ~1 $ y
   la devuelven; **no te cobran nada** mientras uses recursos Always Free.
   - ⚠️ Si la rechazan, espera 24-48 h y reintenta — es un fallo habitual, no un problema tuyo.
3. Región *home*: elige la más cercana, p. ej. **España (Madrid)** o **Amsterdam**.
4. Entra en la consola (Dashboard).

## 2. Crear la máquina virtual (gratis)

1. Menú ☰ (arriba izquierda) → **Compute → Instances** → **Create instance**.
2. **Name**: `optcg-bot`.
3. **Image**: **Ubuntu 24.04** (Minimal). *Contiene Python 3.12, justo lo que necesita el bot.*
4. **Shape**:
   - Marca **"Specialty and legacy"** o busca directamente **`VM.Standard.E2.1.Micro`** (AMD, 1 GB RAM).
   - ✅ Esta es la recomendada: dentro de la cuota Always Free (2 máquinas de este tipo).
   - La alternativa ARM (Ampere A1) da más potencia pero suele dar error de capacidad
     ("out of capacity") y es la que Oracle reclama con más agresividad.
5. **Networking**: deja la red por defecto (se crea sola).
6. **SSH keys**: elige **"Generate a key pair"** → descarga la clave privada
   (**optcg-key.pem**) y guárdala en `C:\Users\luigi\.ssh\`.
7. **Create**. Espera ~2 min hasta que el estado pase a **Running**.
8. Anota la **IP pública** de la instancia.

## 3. Conectarte por SSH (desde Windows)

Abre PowerShell y ejecuta (cambia la IP por la tuya):

```powershell
ssh -i C:\Users\luigi\.ssh\optcg-key.pem ubuntu@<IP_PUBLICA>
```

- La primera vez pregunta `Are you sure...?` → escribe `yes`.
- Si da error de permisos (Windows), ejecuta primero:

```powershell
icacls C:\Users\luigi\.ssh\optcg-key.pem /inheritance:r /grant:r "$env:USERNAME":"(R)"
```

Ya dentro, deberías ver el prompt `ubuntu@optcg-bot:~$`.

## 4. Instalar y lanzar el bot (un solo comando)

```bash
cd ~
curl -sL https://raw.githubusercontent.com/almondiga/TeamVictus/main/deploy/oracle/install_oracle.sh -o install_oracle.sh
chmod +x install_oracle.sh
./install_oracle.sh
```

El instalador hace todo: instala Python y dependencias, clona el repo, te pide las claves
(primera vez) y deja el bot corriendo con auto-reinicio. Te pedirá:

| Variable | Qué pones |
|---|---|
| `DISCORD_TOKEN` | Tu token (no se muestra al teclear) |
| `BERRYWALLET_API_KEY` | `pk_live_...` (o Enter para omitir) |
| `RAPIDAPI_KEY` | Tu clave RapidAPI (o Enter) |
| `OPTCG_API_KEY` | Tu clave optcg (o Enter) |

## 5. Verificar que está vivo

```bash
curl http://127.0.0.1:8080/health
# → {"status": "ok", "bot": "conectando", "guilds": 0}

sudo journalctl -u optcg-bot -n 50 -f
# busca: ✅ Comandos slash sincronizados  y  ⚓ <TEAMVICTUS> conectado
```

- El `/health` responde `ok` aunque el bot aún esté conectando; el log confirma la conexión.
- Los comandos slash (10) se registran globalmente en Discord en el primer arranque
  (tardan ~1 h en aparecer en servidores ya existentes; en uno nuevo son inmediatos).

## 6. Invitar el bot a tu servidor

```text
https://discord.com/oauth2/authorize?client_id=1548107775232577689&scope=bot%20applications.commands&permissions=116736
```

## 7. (Opcional pero recomendado) UptimeRobot + puerto 8080

Sirve para **dos cosas**: vigilar que el bot sigue vivo (te avisa por email si se cae) y
generar tráfico de red que reduce el riesgo de que Oracle reclame la instancia por
"inactividad" (ver nota al final).

1. Abre el puerto 8080 en Oracle: ☰ → **Networking → Virtual cloud networks** → tu VCN →
   **Security Lists** → Default Security List → **Add Ingress Rules** →
   Source: `0.0.0.0/0`, IP Protocol: TCP, Destination Port: `8080`.
2. En **https://uptimerobot.com** (gratis, 50 monitores) → Add New Monitor:
   - Monitor Type: **HTTP(s)**
   - URL: `http://<IP_PUBLICA>:8080/health`
   - Interval: 5 minutes.

## Mantenimiento

```bash
# Actualizar el bot a la última versión del repo
cd /opt/optcg-bot && git pull && sudo systemctl restart optcg-bot

# Ver logs en vivo
sudo journalctl -u optcg-bot -f

# Cambiar claves (por ejemplo, si regeneras el token de Discord)
sudo nano /opt/optcg-bot/.env
sudo systemctl restart optcg-bot
```

## Backup de la base de datos

La BD (`/opt/optcg-bot/optcg_bot.db`) guarda préstamos y colecciones. Para respaldarla:

- En Discord: `/exportar` te da un CSV (restaurar con `/importar`).
- O en la VM: `cp /opt/optcg-bot/optcg_bot.db ~/optcg_bot_backup.db` y descárgala con
  `scp -i C:\Users\luigi\.ssh\optcg-key.pem ubuntu@<IP>:/home/ubuntu/optcg_bot_backup.db .`

## Costes y límites (Always Free, 2026)

| Recurso | Cuota gratis |
|---|---|
| VM AMD `E2.1.Micro` (1/8 OCPU, 1 GB) | 2 instancias |
| Boot volume | 200 GB (todas las instancias) |
| Tráfico de salida | 10 TB/mes |
| Caducidad | No tiene — es "siempre gratis" |

## ⚠️ Nota honesta sobre la reclamación por inactividad

Oracle **puede reclamar** cualquier instancia Always Free que considere "inactiva"
(documentación oficial): CPU < 20 %, red < 20 % (y memoria < 20 % solo en las ARM)
durante 7 días seguidos. Un bot de Discord pasa la mayor parte del tiempo con la CPU
ociosa, así que el riesgo existe en teoría.

**Mitigación práctica:**
1. El monitor de **UptimeRobot cada 5 min** sobre `/health` genera actividad de red.
2. El propio websocket de Discord mantiene tráfico de red constante.
3. La **AMD micro es la menos reclamada** en la práctica (las reclamaciones masivas
   históricas fueron sobre las ARM A1).
4. Y aunque la reclamen: la instancia se **detiene** (no se borra) — puedes volver a
   arrancarla desde la consola y el bot vuelve solo.

Estos mismos archivos (`install_oracle.sh` + servicio) sirven para **cualquier VPS Linux**
(DigitalOcean, Hetzner, un mini-PC...) — solo cambia la URL de descarga.
