#!/usr/bin/env bash
# Create a Debian LXC container on a Proxmox VE host and install AgentMailSender in it.
#
# Run as root on the Proxmox VE host:
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/KorinBlack/AgentMailSender/main/deploy/proxmox/create-ct.sh)"
#   bash -c "$(curl -fsSL …/create-ct.sh)" _ --ip 192.168.1.50/24 --gateway 192.168.1.1
# or from a checkout of the repository (installs that code, pushed or not):
#   bash deploy/proxmox/create-ct.sh --source-dir .
set -Eeuo pipefail

DEFAULT_REPO_URL="https://github.com/KorinBlack/AgentMailSender.git"
DEFAULT_REF="main"

CTID=""
CT_HOSTNAME="agentmailsender"
STORAGE=""
TEMPLATE_STORAGE="local"
TEMPLATE=""
DEBIAN_VERSION=""
DISK_GB=4
MEMORY=512
SWAP=512
CORES=1
BRIDGE="vmbr0"
IP="dhcp"
GATEWAY=""
VLAN=""
NAMESERVER=""
ROOT_PASSWORD=""
SSH_KEY_FILE=""
ONBOOT=1
REPO_URL="$DEFAULT_REPO_URL"
REF="$DEFAULT_REF"
SOURCE_DIR=""
PORT=8080
PUBLIC_URL=""
TRUSTED_PROXY=""
ADMIN_USER="admin"
ASSUME_YES=0

msg() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<EOF
Create a Proxmox VE container (Debian, unprivileged) running AgentMailSender.

Usage: create-ct.sh [options]

Container:
  --ctid ID               Container ID (default: next free ID)
  --hostname NAME         Hostname (default: $CT_HOSTNAME)
  --storage NAME          Storage for the root disk (default: local-lvm, local-zfs or the first suitable one)
  --disk GB               Root disk size in GB (default: $DISK_GB)
  --memory MB             Memory (default: $MEMORY)
  --swap MB               Swap (default: $SWAP)
  --cores N               CPU cores (default: $CORES)
  --template-storage NAME Storage holding container templates (default: $TEMPLATE_STORAGE)
  --debian 12|13          Debian release (default: 13 on Proxmox VE 9+, else 12)
  --template FILE         Exact template name from 'pveam available' (overrides --debian)
  --password PASS         Root password for the container console (default: none, use 'pct enter')
  --ssh-key FILE          Public key file for root SSH logins
  --no-onboot             Don't start the container when the host boots

Network:
  --bridge NAME           Bridge (default: $BRIDGE)
  --ip dhcp|CIDR          'dhcp' or a static address such as 192.168.1.50/24 (default: dhcp)
  --gateway IP            Gateway for a static address
  --vlan TAG              VLAN tag
  --nameserver IP         DNS server (default: the host's)

Application:
  --repo URL              Git repository to install from (default: $DEFAULT_REPO_URL)
  --ref NAME              Branch or tag (default: $DEFAULT_REF)
  --source-dir DIR        Install this local checkout instead of cloning (uploaded to the container)
  --port N                Dashboard port (default: $PORT)
  --public-url URL        Address operators open (default: http://<container IP>:<port>)
  --trusted-proxy IPS     Reverse proxy IPs trusted for X-Forwarded-* headers
  --admin-user NAME       First operator (default: $ADMIN_USER)

  -y, --yes               Don't ask for confirmation
  -h, --help              Show this help
EOF
}

need_value() { [[ $# -ge 2 && -n ${2:-} ]] || die "$1 needs a value (see --help)."; }

while [[ $# -gt 0 ]]; do
  case $1 in
    --ctid) need_value "$@"; CTID=$2; shift 2 ;;
    --hostname) need_value "$@"; CT_HOSTNAME=$2; shift 2 ;;
    --storage) need_value "$@"; STORAGE=$2; shift 2 ;;
    --disk) need_value "$@"; DISK_GB=$2; shift 2 ;;
    --memory) need_value "$@"; MEMORY=$2; shift 2 ;;
    --swap) need_value "$@"; SWAP=$2; shift 2 ;;
    --cores) need_value "$@"; CORES=$2; shift 2 ;;
    --template-storage) need_value "$@"; TEMPLATE_STORAGE=$2; shift 2 ;;
    --debian) need_value "$@"; DEBIAN_VERSION=$2; shift 2 ;;
    --template) need_value "$@"; TEMPLATE=$2; shift 2 ;;
    --password) need_value "$@"; ROOT_PASSWORD=$2; shift 2 ;;
    --ssh-key) need_value "$@"; SSH_KEY_FILE=$2; shift 2 ;;
    --no-onboot) ONBOOT=0; shift ;;
    --bridge) need_value "$@"; BRIDGE=$2; shift 2 ;;
    --ip) need_value "$@"; IP=$2; shift 2 ;;
    --gateway) need_value "$@"; GATEWAY=$2; shift 2 ;;
    --vlan) need_value "$@"; VLAN=$2; shift 2 ;;
    --nameserver) need_value "$@"; NAMESERVER=$2; shift 2 ;;
    --repo) need_value "$@"; REPO_URL=$2; shift 2 ;;
    --ref) need_value "$@"; REF=$2; shift 2 ;;
    --source-dir) need_value "$@"; SOURCE_DIR=$2; shift 2 ;;
    --port) need_value "$@"; PORT=$2; shift 2 ;;
    --public-url) need_value "$@"; PUBLIC_URL=$2; shift 2 ;;
    --trusted-proxy) need_value "$@"; TRUSTED_PROXY=$2; shift 2 ;;
    --admin-user) need_value "$@"; ADMIN_USER=$2; shift 2 ;;
    -y | --yes) ASSUME_YES=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) die "Unknown option: $1 (see --help)" ;;
  esac
done

# --------------------------------------------------------------------------- checks
[[ $EUID -eq 0 ]] || die "Run this script as root on the Proxmox VE host."
for tool in pct pveam pvesm pvesh pveversion; do
  command -v "$tool" >/dev/null || die "'$tool' not found: run this script on a Proxmox VE host."
done
PVE_MAJOR=$(pveversion | sed -n 's#^pve-manager/\([0-9]\+\).*#\1#p')
[[ ${PVE_MAJOR:-0} -ge 8 ]] || die "Proxmox VE 8 or newer is required (found: $(pveversion))."

for value in "$DISK_GB" "$MEMORY" "$SWAP" "$CORES" "$PORT"; do
  [[ $value =~ ^[0-9]+$ ]] || die "Sizes, cores and port must be whole numbers (got '$value')."
done
[[ $IP == dhcp || $IP =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}/[0-9]{1,2}$ ]] ||
  die "--ip must be 'dhcp' or an IPv4 address with prefix, e.g. 192.168.1.50/24."
[[ $IP == dhcp || -n $GATEWAY ]] || warn "Static address without --gateway: the container may not reach the internet."
[[ -z $VLAN || $VLAN =~ ^[0-9]+$ ]] || die "--vlan must be a number."
[[ -z $SSH_KEY_FILE || -f $SSH_KEY_FILE ]] || die "SSH key file not found: $SSH_KEY_FILE"
[[ -z $PUBLIC_URL || $PUBLIC_URL =~ ^https?:// ]] || die "--public-url must start with http:// or https://."
ip link show "$BRIDGE" >/dev/null 2>&1 || die "Bridge '$BRIDGE' does not exist on this host (see --bridge)."

if [[ -z $CTID ]]; then
  CTID=$(pvesh get /cluster/nextid)
fi
[[ $CTID =~ ^[0-9]+$ && $CTID -ge 100 ]] || die "--ctid must be a number of at least 100."
if compgen -G "/etc/pve/nodes/*/lxc/$CTID.conf" >/dev/null || compgen -G "/etc/pve/nodes/*/qemu-server/$CTID.conf" >/dev/null; then
  die "ID $CTID is already used by a container or VM."
fi

ROOTDIR_STORAGES=$(pvesm status -content rootdir 2>/dev/null | awk 'NR > 1 && $3 == "active" { print $1 }' || true)
if [[ -z $STORAGE ]]; then
  for candidate in local-lvm local-zfs; do
    if grep -qx "$candidate" <<<"$ROOTDIR_STORAGES"; then
      STORAGE=$candidate
      break
    fi
  done
  STORAGE=${STORAGE:-$(head -n 1 <<<"$ROOTDIR_STORAGES")}
fi
[[ -n $STORAGE ]] || die "No active storage can hold container disks. Pass one with --storage."
grep -qx "$STORAGE" <<<"$ROOTDIR_STORAGES" || die "Storage '$STORAGE' is not active or can't hold container disks."
TEMPLATE_STORAGES=$(pvesm status -content vztmpl 2>/dev/null | awk 'NR > 1 { print $1 }' || true)
grep -qx "$TEMPLATE_STORAGE" <<<"$TEMPLATE_STORAGES" ||
  die "Storage '$TEMPLATE_STORAGE' can't hold container templates (see --template-storage)."

# --------------------------------------------------------------------------- sources
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

SCRIPT_DIR=""
if [[ -n ${BASH_SOURCE[0]:-} && -f ${BASH_SOURCE[0]} ]]; then
  SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
fi
if [[ -n $SOURCE_DIR ]]; then
  SOURCE_DIR=$(cd "$SOURCE_DIR" && pwd) || die "Directory not found: $SOURCE_DIR"
  [[ -f $SOURCE_DIR/pyproject.toml && -d $SOURCE_DIR/agentmailsender ]] ||
    die "$SOURCE_DIR is not an AgentMailSender checkout."
fi

if [[ -n $SCRIPT_DIR && -f $SCRIPT_DIR/install.sh ]]; then
  INSTALLER=$SCRIPT_DIR/install.sh
elif [[ -n $SOURCE_DIR && -f $SOURCE_DIR/deploy/proxmox/install.sh ]]; then
  INSTALLER=$SOURCE_DIR/deploy/proxmox/install.sh
elif [[ $REPO_URL =~ ^https://github\.com/([^/]+)/([^/]+)$ ]]; then
  owner=${BASH_REMATCH[1]}
  repo=${BASH_REMATCH[2]%.git}
  INSTALLER=$WORK/install.sh
  msg "Downloading the installer from GitHub ($owner/$repo@$REF)"
  curl -fsSL "https://raw.githubusercontent.com/$owner/$repo/$REF/deploy/proxmox/install.sh" -o "$INSTALLER" ||
    die "Could not download install.sh from $owner/$repo ($REF). Has the repository been pushed?"
else
  die "install.sh not found next to this script; use --source-dir or a GitHub --repo URL."
fi
# Files copied from Windows may have CRLF line endings, which bash refuses.
sed 's/\r$//' "$INSTALLER" >"$WORK/agentmailsender-install.sh"

if [[ -n $SOURCE_DIR ]]; then
  msg "Packing $SOURCE_DIR"
  tar -C "$SOURCE_DIR" --exclude=.git --exclude=.venv --exclude=venv --exclude=data --exclude=__pycache__ \
    --exclude='*.egg-info' --exclude=node_modules --exclude=build --exclude=dist \
    -czf "$WORK/agentmailsender-src.tar.gz" .
fi

# --------------------------------------------------------------------------- template
if [[ -z $TEMPLATE ]]; then
  if [[ -z $DEBIAN_VERSION ]]; then
    if [[ $PVE_MAJOR -ge 9 ]]; then DEBIAN_VERSION=13; else DEBIAN_VERSION=12; fi
  fi
  [[ $DEBIAN_VERSION =~ ^1[2-9]$ ]] || die "--debian must be 12 or newer."
  msg "Looking up the latest Debian $DEBIAN_VERSION template"
  pveam update >/dev/null 2>&1 || warn "Could not refresh the template list; using the cached one."
  TEMPLATE=$(pveam available --section system 2>/dev/null | awk '{ print $2 }' |
    grep -E "^debian-${DEBIAN_VERSION}-standard_.*_amd64\.tar\.(zst|gz|xz)$" | sort -V | tail -n 1 || true)
  [[ -n $TEMPLATE ]] || die "No debian-${DEBIAN_VERSION}-standard template is available (try --debian 12)."
fi

# --------------------------------------------------------------------------- confirmation
NET0="name=eth0,bridge=$BRIDGE,ip=$IP"
[[ $IP != dhcp && -n $GATEWAY ]] && NET0+=",gw=$GATEWAY"
[[ -n $VLAN ]] && NET0+=",tag=$VLAN"
if [[ -n $SOURCE_DIR ]]; then SOURCE_LABEL="local checkout $SOURCE_DIR"; else SOURCE_LABEL="$REPO_URL ($REF)"; fi

cat <<EOF

  AgentMailSender container
  ─────────────────────────
  ID / hostname   $CTID / $CT_HOSTNAME
  Template        $TEMPLATE_STORAGE:vztmpl/$TEMPLATE
  Resources       $CORES core(s), $MEMORY MB RAM, $SWAP MB swap, $DISK_GB GB on $STORAGE
  Network         $NET0
  Options         unprivileged, nesting, start at boot: $([[ $ONBOOT -eq 1 ]] && echo yes || echo no)
  Application     $SOURCE_LABEL, port $PORT

EOF
if [[ $ASSUME_YES -ne 1 ]]; then
  read -r -p "Create it? [y/N] " answer </dev/tty || answer=""
  [[ $answer =~ ^[YyJjSs] ]] || die "Cancelled."
fi

# --------------------------------------------------------------------------- create
LOCAL_TEMPLATES=$(pveam list "$TEMPLATE_STORAGE" 2>/dev/null | awk '{ print $1 }' || true)
if ! grep -qx "$TEMPLATE_STORAGE:vztmpl/$TEMPLATE" <<<"$LOCAL_TEMPLATES"; then
  msg "Downloading $TEMPLATE"
  pveam download "$TEMPLATE_STORAGE" "$TEMPLATE" >/dev/null
fi

CREATED=0
on_error() {
  local code=$?
  if [[ $CREATED -eq 1 ]]; then
    printf '\n\033[1;31mThe installation did not finish.\033[0m Container %s was kept so you can inspect it:\n' "$CTID" >&2
    printf '  pct enter %s            # open a shell, then: journalctl -u agentmailsender\n' "$CTID" >&2
    printf '  pct stop %s && pct destroy %s --purge   # remove it\n' "$CTID" "$CTID" >&2
  fi
  exit "$code"
}
trap on_error ERR

msg "Creating container $CTID"
CREATE_ARGS=(
  "$CTID" "$TEMPLATE_STORAGE:vztmpl/$TEMPLATE"
  --hostname "$CT_HOSTNAME"
  --cores "$CORES" --memory "$MEMORY" --swap "$SWAP"
  --rootfs "$STORAGE:$DISK_GB"
  --net0 "$NET0"
  --unprivileged 1
  --features nesting=1
  --onboot "$ONBOOT"
  --timezone host
  --tags agentmailsender
  --description "AgentMailSender: human approval gate for agent e-mail. Dashboard on port $PORT."
)
[[ -n $NAMESERVER ]] && CREATE_ARGS+=(--nameserver "$NAMESERVER")
[[ -n $ROOT_PASSWORD ]] && CREATE_ARGS+=(--password "$ROOT_PASSWORD")
[[ -n $SSH_KEY_FILE ]] && CREATE_ARGS+=(--ssh-public-keys "$SSH_KEY_FILE")
pct create "${CREATE_ARGS[@]}" >/dev/null
CREATED=1

msg "Starting container $CTID"
pct start "$CTID"
msg "Waiting for the network"
online=0
for _ in $(seq 1 90); do
  if pct exec "$CTID" -- sh -c 'getent hosts deb.debian.org >/dev/null 2>&1'; then
    online=1
    break
  fi
  sleep 1
done
if [[ $online -ne 1 ]]; then
  echo "The container has no working network or DNS after 90 seconds. Check --bridge, --ip, --gateway and --nameserver." >&2
  false
fi

msg "Copying the installer"
pct push "$CTID" "$WORK/agentmailsender-install.sh" /root/agentmailsender-install.sh --perms 0755
INSTALL_ARGS=(--port "$PORT" --admin-user "$ADMIN_USER")
if [[ -n $SOURCE_DIR ]]; then
  pct push "$CTID" "$WORK/agentmailsender-src.tar.gz" /root/agentmailsender-src.tar.gz
  INSTALL_ARGS+=(--archive /root/agentmailsender-src.tar.gz)
else
  INSTALL_ARGS+=(--repo "$REPO_URL" --ref "$REF")
fi
[[ -n $PUBLIC_URL ]] && INSTALL_ARGS+=(--public-url "$PUBLIC_URL")
[[ -n $TRUSTED_PROXY ]] && INSTALL_ARGS+=(--trusted-proxy "$TRUSTED_PROXY")

msg "Installing AgentMailSender inside the container"
pct exec "$CTID" -- bash /root/agentmailsender-install.sh "${INSTALL_ARGS[@]}"
pct exec "$CTID" -- rm -f /root/agentmailsender-install.sh /root/agentmailsender-src.tar.gz

trap - ERR
CT_IP=$(pct exec "$CTID" -- sh -c "hostname -I 2>/dev/null | awk '{print \$1}'" || true)
cat <<EOF
Container $CTID ($CT_HOSTNAME) is ready${CT_IP:+ at $CT_IP}.

  Shell in the container:  pct enter $CTID
  Update the app:          pct exec $CTID -- agentmailsender-update
  Reset a password:        pct exec $CTID -- agentmailsender-admin reset-password $ADMIN_USER
  Back up:                 vzdump $CTID   (or a scheduled backup job in the Proxmox UI)

EOF
