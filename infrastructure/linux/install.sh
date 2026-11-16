#!/usr/bin/env bash
# Run once on a fresh Ubuntu 24.04 VM, from the repository root, as a normal user with sudo:
#   bash infrastructure/linux/install.sh
# Installs Python and Node, the pinned dependencies, and the systemd service. It never reads
# or writes .env; copy that and the work/ data across yourself (see README.md here).
set -euo pipefail
cd "$(dirname "$0")/../.."
dir=$(pwd)

sudo apt-get update -q
sudo apt-get install -y -q python3.12 python3.12-venv sqlite3
command -v node >/dev/null && node -e 'process.exit(+process.versions.node.split(".")[0] < 22)' \
  || sudo snap install node --classic --channel=22

[ -x .venv/bin/python ] || python3.12 -m venv .venv
.venv/bin/python -m pip install -q --no-deps -r requirements-dev.lock
.venv/bin/python -m pip install -q --no-build-isolation --no-deps -e .
.venv/bin/python -m pip check
(cd apps/web && npm ci --no-audit --no-fund)
mkdir -p work/logs

sed -e "s#__USER__#$(id -un)#" -e "s#__DIR__#$dir#g" infrastructure/linux/tradepilot.service \
  | sudo tee /etc/systemd/system/tradepilot.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable tradepilot
echo "Installed. Copy .env and work/ across, then: sudo systemctl start tradepilot"
