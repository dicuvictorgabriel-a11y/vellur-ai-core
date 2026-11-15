#!/usr/bin/env bash
# install.sh — installs or updates VELLUR AI CORE on the VPS. Safe to run again (idempotent).
#
#   bash install.sh [--install-docker] [--add-swap]
#
# What it does, in order (it stops at the first problem, before touching anything shared):
#   1. read-only discovery report (discover.sh) and resource checks
#   2. optional, only with your "yes": 2 GB swap file; Docker from Ubuntu packages (if missing)
#   3. copies the project to /opt/vellur-ai-core, generates secrets in .env (mode 600)
#   4. starts the gateway (LiteLLM, 127.0.0.1 only), creates the first client key ("opencode")
#   5. publishes https://ai.vellurdigital.com through the EXISTING Caddy — only if DNS already
#      points here — with backup, validation and automatic restore (sudo vac expose)
#   6. runs the checks (sudo vac verify)
#   4b. starts the GPU Autopilot controller in DRY RUN (dashboard https://<domain>/autopilot/, login shown once)
# It never stops, restarts or edits other projects, containers, firewall rules or DNS.
set -euo pipefail

if [ "$(id -u)" != 0 ]; then exec sudo -E bash "$0" "$@"; fi

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="${VAC_HOME:-/opt/vellur-ai-core}"
YES_DOCKER=0; YES_SWAP=0
for a in "$@"; do case $a in --install-docker) YES_DOCKER=1 ;; --add-swap) YES_SWAP=1 ;; esac; done

c_ok=$'\e[32m'; c_warn=$'\e[33m'; c_bad=$'\e[31m'; c_b=$'\e[1m'; c_off=$'\e[0m'
step() { echo; echo "${c_b}── $*${c_off}"; }
ok()   { echo "${c_ok}✓${c_off} $*"; }
warn() { echo "${c_warn}!${c_off} $*"; }
die()  { echo "${c_bad}✗ $*${c_off}" >&2; echo "Nothing else was changed." >&2; exit 1; }
ask()  { local a; read -r -p "$1 [y/N] " a || a=""; [[ $a =~ ^[Yy]$ ]]; }
port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

# Files checked out on Windows may carry CRLF line endings; bash needs LF.
find "$SRC" -type f \( -name '*.sh' -o -name 'vac' -o -name '*.yaml' -o -name '*.example' -o -name '*.py' -o -name 'Dockerfile' -o -name '*.txt' \) -exec sed -i 's/\r$//' {} +

step "1/6 Discovery (read-only)"
mkdir -p /var/backups/vellur-ai-core; chmod 700 /var/backups/vellur-ai-core
REPORT="/var/backups/vellur-ai-core/discovery-$(date +%Y%m%d-%H%M%S).txt"
bash "$SRC/discover.sh" > "$REPORT" 2>&1 || true
chmod 600 "$REPORT"
sed -n '/== System/,/== Top memory/p' "$REPORT" | head -12
ok "Full report: $REPORT"

MEM_AVAIL=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
MEM_TOTAL=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
SWAP_TOTAL=$(awk '/SwapTotal/ {print int($2/1024)}' /proc/meminfo)
DISK_FREE=$(df -Pm / | awk 'NR==2 {print $4}')
echo "RAM ${MEM_TOTAL} MB (available ${MEM_AVAIL} MB), swap ${SWAP_TOTAL} MB, free disk ${DISK_FREE} MB"
[ "$DISK_FREE" -ge 5000 ] || die "Less than 5 GB free disk. Free space first."
[ "$MEM_AVAIL" -ge 1200 ] || die "Less than 1.2 GB RAM available; the gateway needs up to 1 GB. Not installing, to protect the other projects."

step "2/6 Prerequisites"
if [ "$SWAP_TOTAL" -eq 0 ] && [ "$MEM_TOTAL" -lt 6000 ]; then
	echo "This server has no swap. A 2 GB swap file protects all projects from out-of-memory kills during peaks"
	echo "(swappiness 10: used only under pressure). Reversible: sudo swapoff /swapfile; remove the line in /etc/fstab."
	if [ $YES_SWAP = 1 ] || ask "Create a 2 GB swap file now?"; then
		[ "$DISK_FREE" -ge 8000 ] || die "Not enough disk for a swap file."
		cp -p /etc/fstab "/etc/fstab.bak-vellur-ai-$(date +%Y%m%d-%H%M%S)"
		fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
		grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
		echo 'vm.swappiness=10' > /etc/sysctl.d/99-vellur-ai-swap.conf; sysctl -q -p /etc/sysctl.d/99-vellur-ai-swap.conf
		ok "Swap active: $(swapon --show=NAME,SIZE --noheadings)"
	else warn "Skipped swap."; fi
fi

if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
	echo "Docker (with the compose plugin) is needed to isolate VELLUR AI CORE from the other projects."
	echo "It will be installed from the Ubuntu packages (docker.io, docker-compose-v2). Existing services are not restarted."
	echo "All VELLUR ports are bound to 127.0.0.1, so Docker's firewall rules expose nothing publicly."
	if [ $YES_DOCKER = 1 ] || ask "Install Docker now?"; then
		apt-get update -q
		if command -v docker >/dev/null; then apt-get install -y -q docker-compose-v2 || apt-get install -y -q docker-compose-plugin
		else apt-get install -y -q docker.io docker-compose-v2; fi
		systemctl enable --now docker
	else die "Docker is required. Run again when you are ready (or with --install-docker)."; fi
fi
ok "$(docker --version)  /  $(docker compose version | head -1)"
command -v openssl >/dev/null || apt-get install -y -q openssl
command -v curl >/dev/null || apt-get install -y -q curl

HAVE_CADDY=0
if command -v caddy >/dev/null && [ -f "${CADDYFILE:-/etc/caddy/Caddyfile}" ]; then
	HAVE_CADDY=1; ok "Existing Caddy found: $(caddy version | cut -d' ' -f1) (will only get one import line)"
else warn "No Caddy with /etc/caddy/Caddyfile found: the stack will run, but will not be published."; fi

step "3/6 Files → $DEST"
if [ -d "$DEST" ]; then
	PRE="/var/backups/vellur-ai-core/pre-update-$(date +%Y%m%d-%H%M%S).tar.gz"
	tar -C "$DEST" -czf "$PRE" . && chmod 600 "$PRE" && ok "Previous installation backed up: $PRE"
fi
install -d -m 755 "$DEST" "$DEST/litellm" "$DEST/bin" "$DEST/www" "$DEST/tunnel" "$DEST/gpu" "$DEST/docs" "$DEST/opencode" \
	"$DEST/autopilot" "$DEST/autopilot/app" "$DEST/autopilot/app/providers" "$DEST/autopilot/app/static"
install -d -m 700 "$DEST/secrets"
install -m 644 "$SRC/compose.yaml" "$SRC/env.example" "$DEST/"
install -m 644 "$SRC/litellm/config.yaml" "$SRC/litellm/models.gpu.yaml" "$SRC/litellm/models.example.yaml" "$DEST/litellm/"
[ -f "$DEST/litellm/models.yaml" ] || install -m 644 "$SRC/litellm/models.yaml" "$DEST/litellm/"   # never overwrite your models
install -m 755 "$SRC/bin/vac" "$DEST/bin/vac"
install -m 755 "$SRC/bin/vac-autoupdate" "$DEST/bin/vac-autoupdate"
install -m 644 "$SRC/tunnel/Dockerfile" "$SRC/tunnel/tunnel.sh" "$DEST/tunnel/"
install -m 644 "$SRC/www/"* "$DEST/www/"
install -m 755 "$SRC/gpu/"*.sh "$DEST/gpu/"
install -m 644 "$SRC/README.md" "$DEST/"; [ -d "$SRC/docs" ] && install -m 644 "$SRC/docs/"* "$DEST/docs/"
install -m 644 "$SRC/opencode/"* "$DEST/opencode/"
install -m 644 "$SRC/autopilot/Dockerfile" "$SRC/autopilot/requirements.txt" "$SRC/autopilot/.dockerignore" "$DEST/autopilot/"
install -m 644 "$SRC/autopilot/app/"*.py "$DEST/autopilot/app/"
install -m 644 "$SRC/autopilot/app/providers/"*.py "$DEST/autopilot/app/providers/"
cp -a "$SRC/autopilot/app/static/." "$DEST/autopilot/app/static/"   # includes subdirs (3d/vendor)
install -d -m 755 "$DEST/agent"
install -m 644 "$SRC/agent/Dockerfile" "$SRC/agent/runner.py" "$DEST/agent/"
install -m 755 "$SRC/agent/vellur-screenshot" "$DEST/agent/"
install -m 644 "$SRC/litellm/models.gpu.yaml" "$DEST/litellm/"
ln -sf "$DEST/bin/vac" /usr/local/bin/vac
ok "Installed; command: sudo vac"

if [ ! -f "$DEST/.env" ]; then
	WP=3310; GP=3311
	while port_busy $WP; do WP=$((WP + 2)); done
	while port_busy $GP || [ $GP = $WP ]; do GP=$((GP + 2)); done
	umask 077
	sed -e "s|^LITELLM_MASTER_KEY=.*|LITELLM_MASTER_KEY=sk-vellur-master-$(openssl rand -hex 32)|" \
	    -e "s|^WEBUI_SECRET_KEY=.*|WEBUI_SECRET_KEY=$(openssl rand -hex 32)|" \
	    -e "s|^WEBUI_PORT=.*|WEBUI_PORT=$WP|" -e "s|^GATEWAY_PORT=.*|GATEWAY_PORT=$GP|" \
	    "$SRC/env.example" > "$DEST/.env"
	chmod 600 "$DEST/.env"; umask 022
	ok "Secrets generated in $DEST/.env (root only). Loopback ports: gateway $GP, web UI $WP"
else ok "Keeping existing $DEST/.env"; fi

step "4/6 Start"
vac start
for i in $(seq 1 40); do [ "$(docker inspect -f '{{.State.Health.Status}}' vellur-ai-gateway 2>/dev/null)" = healthy ] && break; sleep 3; done
[ "$(docker inspect -f '{{.State.Health.Status}}' vellur-ai-gateway)" = healthy ] || die "Gateway not healthy. See: sudo vac logs gateway"
ok "Gateway healthy"
if [ -z "$(awk 'NF==2 && $1 !~ /^#/' "$DEST/secrets/gateway-clients" 2>/dev/null)" ]; then
	vac keys add opencode
fi

step "4b GPU Autopilot (dry run: no paid action possible)"
if [ "${VAC_SKIP_AUTOPILOT:-0}" != 1 ]; then
	MEM_NOW=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
	if [ "$MEM_NOW" -ge 500 ]; then vac autopilot enable || warn "Autopilot did not start; the gateway still works. Logs: sudo vac logs autopilot"
	else warn "Skipped Autopilot: less than 500 MB RAM free."; fi
	# Coding-agent workspace: an isolated container (image ≈ 1.5 GB on disk, ≤ 2 GB RAM only while a task runs).
	DISK_NOW=$(df -Pm / | awk 'NR==2 {print $4}')
	if [ "$MEM_NOW" -ge 2500 ] && [ "$DISK_NOW" -ge 8000 ]; then
		vac agent enable || warn "The agent workspace did not start; everything else works. Logs: sudo vac logs agent"
	else warn "Skipped the agent workspace (needs 2.5 GB free RAM and 8 GB disk): sudo vac agent enable"; fi
fi

step "5/6 Publish (HTTPS)"
EXPOSED=0
if [ $HAVE_CADDY = 1 ]; then
	set +e; vac expose; rc=$?; set -e
	[ $rc = 0 ] && EXPOSED=1
	[ $rc = 2 ] && warn "Waiting for DNS. After adding the record: sudo vac expose"
	[ $rc = 1 ] && warn "Caddy refused the change and the old config was restored. Nothing public changed."
fi

step "6/6 Checks"
vac verify || true

# Keep a copy of what was just installed, so `sudo vac autoupdate rollback` can always return to it.
# (Releases installed by vac-autoupdate are already kept under releases/ and are tracked by that script.)
case "$SRC" in
	"$DEST"/releases/*) ;;
	*)
		rid="manual-$(date -u +%Y%m%d-%H%M%S)"
		mkdir -p "$DEST/releases/$rid"
		if tar -C "$SRC" --exclude=.git --exclude=.venv --exclude=node_modules -cf - . | tar -C "$DEST/releases/$rid" -xf -; then
			old_rel=$(cat "$DEST/.release-current" 2>/dev/null || true)
			[ -n "$old_rel" ] && [ -d "$old_rel" ] && echo "$old_rel" > "$DEST/.release-prev"
			echo "$DEST/releases/$rid" > "$DEST/.release-current"; : > "$DEST/.release-sha"
			for d in "$DEST"/releases/*; do
				[ -d "$d" ] && [ "$d" != "$(cat "$DEST/.release-current")" ] && [ "$d" != "$(cat "$DEST/.release-prev" 2>/dev/null)" ] && rm -rf -- "$d"
			done
		fi ;;
esac

echo
echo "${c_b}Done.${c_off}"
[ $EXPOSED = 1 ] && echo "  Status page and gateway: https://$(sed -n 's/^AI_DOMAIN=//p' "$DEST/.env")  (gateway: …/v1)"
echo "  Everyday commands: sudo vac status | verify | logs | keys"
echo "  GPU later (when approved): see $DEST/docs/GPU-AND-MODELS.md — sudo vac gpu init"
