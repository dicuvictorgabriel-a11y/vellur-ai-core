#!/usr/bin/env bash
# discover.sh — READ-ONLY inventory of the VPS for VELLUR AI CORE. Changes nothing.
# Prints no secrets: no file contents with credentials, no environment values, no keys.
#   bash discover.sh            (sudo is used, when available, only to read more accurately)
set -uo pipefail
S=""; [ "$(id -u)" = 0 ] || { sudo -n true 2>/dev/null && S="sudo"; }
h() { printf '\n== %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

h "System"
. /etc/os-release 2>/dev/null; echo "OS: ${PRETTY_NAME:-unknown}   kernel: $(uname -r)   arch: $(uname -m)"
echo "host: $(hostname)   virt: $(systemd-detect-virt 2>/dev/null || echo ?)   uptime: $(uptime -p 2>/dev/null)"
echo "CPU: $(nproc) x $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | sed 's/^ //')"
echo "load: $(cut -d' ' -f1-3 /proc/loadavg)"
free -m | sed 's/^/  /'
df -h -x tmpfs -x devtmpfs -x overlay 2>/dev/null | sed 's/^/  /'
echo "swap devices:"; swapon --show 2>/dev/null | sed 's/^/  /' || echo "  none"

h "Top memory users"
ps -eo rss=,comm= --sort=-rss | head -12 | awk '{printf "  %6d MB  %s\n", $1/1024, $2}'

h "GPU"
if have nvidia-smi; then nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | sed 's/^/  /'
else echo "  no nvidia-smi"; (have lspci && lspci | grep -iE 'vga|3d|nvidia' | sed 's/^/  /') || true; fi

h "Listening TCP ports (address → process)"
$S ss -ltnpH 2>/dev/null | awk '{split($NF,p,"\""); printf "  %-28s %s\n", $4, p[2]}' | sort -u

h "Running services (systemd)"
systemctl list-units --type=service --state=running --no-legend --plain 2>/dev/null | awk '{print "  " $1}'

h "Docker"
if have docker; then
	$S docker version --format '  engine {{.Server.Version}}' 2>/dev/null || echo "  docker CLI present, daemon not reachable"
	$S docker compose version 2>/dev/null | sed 's/^/  /' || echo "  compose plugin: missing"
	echo "  containers:"; $S docker ps -a --format '    {{.Names}}  [{{.Image}}]  {{.Status}}  {{.Ports}}' 2>/dev/null
	echo "  networks:"; $S docker network ls --format '    {{.Name}} ({{.Driver}})' 2>/dev/null
	echo "  disk:"; $S docker system df 2>/dev/null | sed 's/^/    /'
else echo "  not installed"; fi

h "Reverse proxy / TLS"
if have caddy; then
	echo "  caddy $(caddy version 2>/dev/null | cut -d' ' -f1)   service: $(systemctl is-active caddy 2>/dev/null)"
	if $S test -r /etc/caddy/Caddyfile; then
		echo "  sites in /etc/caddy/Caddyfile (addresses only):"
		$S awk '/^[^ \t#{(][^{]*\{[ \t]*$/ {sub(/[ \t]*\{[ \t]*$/,""); print "    " $0} /^import / {print "    (" $0 ")"}' /etc/caddy/Caddyfile
	fi
	for d in /var/lib/caddy/.local/share/caddy/certificates /root/.local/share/caddy/certificates; do
		$S test -d "$d" && { echo "  certificates in $d:"; $S find "$d" -name '*.crt' -printf '    %f\n' 2>/dev/null; }
	done
fi
for p in nginx apache2 httpd traefik haproxy; do have $p && echo "  also installed: $p ($(systemctl is-active $p 2>/dev/null))"; done
have certbot && echo "  certbot present"

h "Firewall"
if have ufw; then $S ufw status verbose 2>/dev/null | sed 's/^/  /'; fi
have nft && echo "  nftables tables: $($S nft list tables 2>/dev/null | tr '\n' ' ')"
echo "  iptables INPUT policy: $($S iptables -S INPUT 2>/dev/null | head -1)"
echo "  ip_forward: $(cat /proc/sys/net/ipv4/ip_forward)"
ip -br link 2>/dev/null | grep -E '^(wg|tun|tailscale|zt)' | sed 's/^/  VPN interface: /'

h "Network / DNS"
echo "  addresses: $(hostname -I 2>/dev/null)"
for d in vellurdigital.com ai.vellurdigital.com gateway.ai.vellurdigital.com bloom.vellurdigital.com; do
	printf '  %-32s %s\n' "$d" "$(getent ahosts "$d" 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' ')"
done
have dig && echo "  NS: $(dig +short NS vellurdigital.com | tr '\n' ' ')"

h "Ports VELLUR AI CORE would use (127.0.0.1)"
for p in 3310 3311; do (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null && echo "  $p in use (fine if it is vellur-ai-core itself)" || echo "  $p free"; done

h "Tools / integrations"
for t in git gh curl openssl ssh-keygen node python3; do printf '  %-11s %s\n' "$t" "$(command -v $t >/dev/null && echo yes || echo no)"; done
ls ~/.ssh 2>/dev/null | sed 's/^/  ~\/.ssh: /'

h "Assessment for VELLUR AI CORE"
mt=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo); ma=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
echo "  RAM total ${mt} MB, available ${ma} MB."
[ "$ma" -ge 1200 ] && echo "  gateway (LiteLLM, ≤1 GB, ~0.6 GB used): fits" || echo "  gateway (LiteLLM, ≤1 GB): TOO TIGHT — free memory first"
[ "$ma" -ge 2800 ] && echo "  Open WebUI as well (≤1.5 GB): fits" || echo "  Open WebUI (≤1.5 GB): not recommended on this server now — use the status page"
echo "  local CPU inference of a coding model: NOT recommended ($(nproc) vCPU; it would compete with production)"
