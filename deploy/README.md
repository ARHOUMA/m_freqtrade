# Dry-run the SMC strategy on an OVH VPS

This folder runs two **paper-trading** bots 24/7 on a server, using live Binance prices but
simulated orders: no API keys and no real money.

| Bot | Market | Strategy | Web UI (through SSH) |
|---|---|---|---|
| `smc-spot` | Binance spot, 12 pairs, longs only | `SMCCombo_ModelGuard` | http://localhost:8080 |
| `smc-futures` | Binance USDT futures, same 12 pairs, longs + shorts | `SMCCombo_ModelGuard` | http://localhost:8081 |

Each bot starts with a 1,000 USDT paper wallet and at most 4 open trades.

---

## 1. Get a VPS

In the OVHcloud Control Panel: **Bare Metal Cloud → Virtual private servers → Order**
(or use a VPS you already have).

- **Model:** the smallest one is enough (at least 2 GB RAM). The bots use about 1 GB together.
- **OS:** Ubuntu 24.04.
- **Location:** a European datacentre (France, Germany, UK, Poland...). **Not the US**: Binance
  refuses connections from US IP addresses.
- **SSH key:** if the order form offers it, paste your public key. On your computer:
  `ssh-keygen -t ed25519` (press Enter at each question) once, then
  `cat ~/.ssh/id_ed25519.pub` and copy the line.

OVH emails you the server's **IP address** and **user name** (usually `ubuntu`).

## 2. Connect to the server

On your computer:

```bash
ssh ubuntu@YOUR_VPS_IP
```

Answer `yes` the first time. If you did not add an SSH key, use the password from the OVH email.
Every command below, until step 8, is typed **on the server**.

## 3. Update and protect the server

```bash
sudo apt update && sudo apt upgrade -y
```

```bash
sudo ufw allow OpenSSH && sudo ufw enable
```

The firewall only lets SSH in. The bots' web pages listen on the server itself only, and you
reach them through SSH (step 8), so nothing else needs to be opened.

## 4. Install Docker and git

```bash
sudo apt install -y docker.io docker-compose-v2 git
```

```bash
sudo usermod -aG docker $USER
```

Log out (`exit`) and connect again with `ssh` so the change applies, then check:

```bash
docker compose version
```

## 5. Download the project

Do this as the normal `ubuntu` user, **not root** (the bots must be able to write to
`user_data`):

```bash
git clone https://github.com/ARHOUMA/m_freqtrade.git
```

```bash
cd m_freqtrade/deploy
```

## 6. Create the password file

This generates a random password for the web UI and saves it in `deploy/.env` (that file is
never committed to git):

```bash
cat > .env <<EOF
FREQTRADE__API_SERVER__USERNAME=admin
FREQTRADE__API_SERVER__PASSWORD=$(openssl rand -hex 12)
FREQTRADE__API_SERVER__JWT_SECRET_KEY=k$(openssl rand -hex 32)
FREQTRADE__API_SERVER__WS_TOKEN=k$(openssl rand -hex 16)
EOF
chmod 600 .env
```

Show the password and write it down - you need it in step 8:

```bash
grep PASSWORD .env
```

## 7. Start the bots

```bash
docker compose pull
```

```bash
docker compose up -d
```

Check that both are `Up`:

```bash
docker compose ps
```

Watch the log (Ctrl+C stops watching; the bots keep running):

```bash
docker compose logs -f
```

A healthy start shows, for each bot, `Dry run is enabled`, `Using resolved strategy
SMCCombo_ModelGuard`, `Changing state to: RUNNING` and then a `Bot heartbeat` line every few
minutes. Lines with `ERROR` or a Python `Traceback` mean something is wrong.

`SECURITY WARNING - Local Rest Server listening to external connections` is expected: inside
its container the bot must listen on all addresses, but Docker only publishes the port on the
server's own loopback address (`127.0.0.1`), so it is not reachable from the internet.

## 8. Open the web UI from your computer

On **your computer** (a new terminal, not the server):

```bash
ssh -N -L 8080:127.0.0.1:8080 -L 8081:127.0.0.1:8081 ubuntu@YOUR_VPS_IP
```

It looks like nothing happens - that is normal; leave that window open while you use the UI.
Then open http://localhost:8080 in your browser and log in:

- API URL: `http://localhost:8080`
- Username: `admin`
- Password: the one from step 6

To add the futures bot, open the bot menu at the top, choose **Add new bot**, and use
`http://localhost:8081` with the same username and password.

## 9. Optional: Telegram notifications

Get a message on your phone for every trade.

1. In Telegram, talk to **@BotFather**, send `/newbot` and follow the questions. Do it **twice**
   (one Telegram bot per trading bot). Keep both tokens.
2. Open each new bot and press **Start**.
3. Talk to **@userinfobot** to get your chat id.
4. On the server, in `~/m_freqtrade/deploy`, add to `.env` (`nano .env`, save with Ctrl+O,
   Enter, exit with Ctrl+X):

   ```
   SPOT_TELEGRAM_ENABLED=true
   SPOT_TELEGRAM_TOKEN=first-token
   FUTURES_TELEGRAM_ENABLED=true
   FUTURES_TELEGRAM_TOKEN=second-token
   TELEGRAM_CHAT_ID=your-chat-id
   ```

5. Apply it with `docker compose up -d`, then send `/status` to either bot.

---

## Everyday commands

Run them on the server in `~/m_freqtrade/deploy`.

| To... | Command |
|---|---|
| See if the bots run | `docker compose ps` |
| Read the last log lines | `docker compose logs --tail 100 smc-futures` |
| Stop both bots | `docker compose down` |
| Start them again | `docker compose up -d` |
| Use the latest strategy code from GitHub | `git pull && docker compose restart` |
| Update freqtrade itself | `docker compose pull && docker compose up -d` |
| Reset the paper wallets and trade history | `docker compose down && rm ../user_data/dryrun_smc_*.sqlite && docker compose up -d` |

The bots restart by themselves after a crash or a server reboot.

## What to expect

- The strategy uses **daily candles**. It makes its decisions once a day, just after
  **00:00 UTC** (01:00 in Paris in winter, 02:00 in summer). Stop-losses and the inverse FVG
  targets can trigger at any time of day.
- In backtests it traded about **30 times a year on spot and 55 on futures**, so expect a few
  trades a month per bot, and quiet weeks are normal.
- Give it **at least 2-3 months** before drawing conclusions, then compare with a backtest
  over the same dates on your computer, for example:

  ```bash
  freqtrade backtesting -c user_data/config_smc.json --strategy SMCCombo_ModelGuard --timerange 20261201- --enable-protections
  ```

- A dry run fills orders at the live price with Binance's fees, but it cannot show real
  slippage on big orders. With 1,000 USDT that does not matter.
