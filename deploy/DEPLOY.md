# Deploying the Autonomous Agent

This is the one part of the build that has to happen on the actual VM
— the dev sandbox this was built in can reach GitHub, PyPI, and
Anthropic's API, but not Alpaca's trading/data hosts, so live
verification and deployment both have to happen where network access
is confirmed working.

## 1. Pull the repo

```bash
ssh ubuntu@158.179.21.90
cd ~
git clone https://github.com/irishkiwi007/alpaca-options-agent.git
cd alpaca-options-agent
```

If it's already cloned from earlier testing:

```bash
cd ~/alpaca-options-agent
git pull origin main
```

## 2. Install dependencies

```bash
pip install -r requirements-agent.txt --break-system-packages
```

## 3. Set up `.env`

```bash
cp .env.example .env
nano .env   # or vim, whatever's available
```

Fill in the same four values already in use:
- `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` — the "Hackathon" paper account keys
- `ANTHROPIC_API_KEY` — can reuse the same key your other bots use
- Leave `ALPACA_BASE_URL` / `ALPACA_DATA_URL` / `ANTHROPIC_MODEL` as the defaults in `.env.example`

## 4. Verify before deploying as a service

Run one cycle manually first, so any problem is visible in your terminal rather than buried in `systemctl status` output:

```bash
python3 -c "
import asyncio
from agent_layer.autonomous_agent import AutonomousTradingAgent
asyncio.run(AutonomousTradingAgent().run_cycle())
"
```

Check `logs/events.jsonl` afterward — you should see `agent_reasoning` and `agent_tool_call` events with real data (account equity, live quotes), not the SSL/connection errors seen in the dev sandbox.

## 5. Install as a systemd service

```bash
sudo cp deploy/alpaca-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable alpaca-agent
sudo systemctl start alpaca-agent
```

## 6. Confirm it's running

```bash
sudo systemctl status alpaca-agent
journalctl -u alpaca-agent -f          # live log tail
tail -f logs/events.jsonl               # structured decision/trade log
```

## Leaving it a note

If you need to correct something it's assumed or flag information it should factor in — without stopping the process — write a note file:

```bash
echo "Your correction or note here." > ~/alpaca-options-agent/OPERATOR_NOTE
```

The next cycle reads it, treats it as the first thing to consider that cycle, and deletes the file so it's only injected once. No restart needed — it's picked up automatically whenever the next cycle begins.

## Stopping it

**Hard stop (immediate, no flatten):**
```bash
sudo systemctl stop alpaca-agent
```

**Graceful stop (flattens all positions first, then exits):**
```bash
touch ~/alpaca-options-agent/STOP_AND_FLATTEN
```
The loop checks for this file at the start of each cycle (so it may take up to `NEXT_CHECK_MINUTES` — chosen by the agent each cycle, typically well under an hour — to notice). Once it flattens and exits, the file is removed automatically and the process exits cleanly — systemd's `Restart=on-failure` policy does not restart on a clean exit, so it stays stopped. Run `sudo systemctl start alpaca-agent` when you want it running again.

## Automatic stop (no action needed)

If account equity ever drops 15% from that day's starting baseline, the agent flattens all positions and exits on its own — logged as `auto_flatten_triggered` / `autonomous_runner_stopped` in `events.jsonl`. This is a clean, intentional exit (code 0), and since the systemd unit uses `Restart=on-failure` — which only restarts on crashes, not clean exits — **it will not automatically restart itself** after a drawdown stop. The same is true of the graceful `STOP_AND_FLATTEN` stop above. Only genuine crashes trigger an automatic restart; both intentional stop paths stay stopped until you run `sudo systemctl start alpaca-agent` again yourself.
