# Always-on Linux host

The same paper stack as `scripts/start-all-local.ps1`, run by systemd on a small cloud VM, so a
home PC that crashes or sleeps no longer costs trading sessions. Simulated paper trading only;
nothing here enables live trading or places broker orders.

## 1. The VM (owner's choice and account)

Any Ubuntu 24.04 VM with 2 vCPU, 4 GB RAM and a 40 GB disk, in or near Mumbai. A Mumbai region
keeps quote latency low; NSE does not matter here because no orders are sent. For example, an
AWS Lightsail or EC2 instance in `ap-south-1`, an Azure VM in Central India, or a DigitalOcean
droplet in Bangalore. Allow SSH (port 22) only. Do not open ports 3000 or 8000: the API and web
app listen on 127.0.0.1, and you reach them over an SSH tunnel.

## 2. Install

```bash
git clone https://github.com/manoj-be09/TradePilot.git
cd TradePilot
bash infrastructure/linux/install.sh
```

## 3. Move the data across

Stop the Windows stack first (Administrator PowerShell, see the restart command in the README),
so the SQLite file is not copied mid-write. Then copy `.env` and `work/` from the PC. `work/`
holds the database, the forward plans and the recorder data:

```bash
scp .env user@vm:~/TradePilot/.env
scp -r work user@vm:~/TradePilot/
```

Then on the VM, `chmod 600 .env` and `sudo systemctl start tradepilot`. Keep the Windows
scheduled task disabled (`Disable-ScheduledTask -TaskName 'TradePilot local paper'`), so two
copies never trade the same accounts.

## 4. Check it

```bash
systemctl status tradepilot
tail -f work/logs/supervisor.log
```

To open the dashboard from the PC:

```bash
ssh -L 3000:127.0.0.1:3000 -L 8000:127.0.0.1:8000 user@vm
```

Then browse to http://127.0.0.1:3000.

## 5. Phone alert when it is down

Do this step even before you move to a VM. The `heartbeat` service pings an outside watchdog
every minute while the paper exit worker is healthy, and sends `/fail` when it is not. If the
machine dies, pings stop and the watchdog alerts you, which a check on the same machine cannot
do.

1. Create a free check at healthchecks.io. Set the period to 1 minute and the grace to 5 minutes,
   and add its Telegram or email integration.
2. Add one line to `.env`: `HEARTBEAT_URL=<the check's ping URL>`. Treat the URL as a secret.
3. Restart the stack.

The heartbeat logs `Worker healthy` once a minute in `work/logs/heartbeat.log`.

Telegram trade alerts are separate. They come from `alert-relay`, which needs
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` and `TELEGRAM_ALERT_USER_EMAIL`.

## Outage policy

This is what the code already does after an outage. It is written down so results can be read
against it:

- **No catch-up entries.** A runner whose entry window passed while it was down records
  `WINDOW_PASSED` and skips that day, rather than entering late at a different price.
- **Exits at the first quote back.** When the worker restarts, it evaluates every open position's
  stop, target and square-off at the next live quote. A stop crossed during the outage fills
  there, so the loss can exceed the stop.
- **Outage days are not evidence.** A session with a heartbeat alert counts as missing, not as a
  result for or against a strategy.
