#!/usr/bin/env bash
# Install or update AgentMailSender as a systemd service on Debian 12+ / Ubuntu 24.04+.
#
# Run as root inside the container (create-ct.sh does this for you on Proxmox VE):
#   bash install.sh                                  # install from the default Git repository
#   bash install.sh --archive /root/src.tar.gz       # install from an uploaded source archive
#   bash install.sh --port 8080 --public-url https://mail-gate.example.com --trusted-proxy 192.168.1.10
#
# Running it again updates the code and keeps configuration and data (agentmailsender-update does that).
set -Eeuo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export DEBIAN_FRONTEND=noninteractive DEBCONF_NOWARNINGS=yes LANG=C.UTF-8 LC_ALL=C.UTF-8

DEFAULT_REPO_URL="https://github.com/KorinBlack/AgentMailSender.git"
DEFAULT_REF="main"

APP_USER=agentmailsender
APP_DIR=/opt/agentmailsender
SRC_DIR=$APP_DIR/src
VENV_DIR=$APP_DIR/venv
DATA_DIR=/var/lib/agentmailsender
CONF_DIR=/etc/agentmailsender
ENV_FILE=$CONF_DIR/agentmailsender.env
INSTALL_CONF=$CONF_DIR/install.conf
SERVICE=agentmailsender
UNIT_FILE=/etc/systemd/system/$SERVICE.service
DROPIN_DIR=/etc/systemd/system/$SERVICE.service.d
HARDENING_DROPIN=$DROPIN_DIR/10-hardening.conf
CREDENTIALS_FILE=/root/agentmailsender-credentials.txt

REPO_URL=""
REF=""
ARCHIVE=""
LOCAL_DIR=""
PORT=""
PUBLIC_URL=""
TRUSTED_PROXY=""
ADMIN_USER="admin"
UPDATE_ONLY=0

msg() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok() { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }
trap 'printf "\033[1;31mError:\033[0m the installer stopped at line %s.\n" "$LINENO" >&2' ERR

usage() {
  cat <<EOF
Install or update AgentMailSender as a systemd service.

Usage: $0 [options]

Source (default: the Git repository saved by the previous run, else $DEFAULT_REPO_URL):
  --repo URL            Git repository to install from
  --ref NAME            Branch or tag (default: $DEFAULT_REF)
  --archive FILE        Install from a .tar.gz of the source tree
  --src-dir DIR         Install from a source directory on this machine

Configuration (kept on later runs unless given again):
  --port N              Listening port (default: 8080)
  --public-url URL      Address operators open, used in notification links
                        (default: http://<container IP>:<port>)
  --trusted-proxy IPS   Reverse proxy IPs trusted for X-Forwarded-* headers (default: 127.0.0.1)
  --admin-user NAME     First operator, created on a fresh install (default: admin)

  --update              Update the code only; fail instead of installing from scratch
  -h, --help            Show this help
EOF
}

need_value() { [[ $# -ge 2 && -n ${2:-} ]] || die "$1 needs a value (see --help)."; }

while [[ $# -gt 0 ]]; do
  case $1 in
    --repo) need_value "$@"; REPO_URL=$2; shift 2 ;;
    --ref) need_value "$@"; REF=$2; shift 2 ;;
    --archive) need_value "$@"; ARCHIVE=$2; shift 2 ;;
    --src-dir) need_value "$@"; LOCAL_DIR=$2; shift 2 ;;
    --port) need_value "$@"; PORT=$2; shift 2 ;;
    --public-url) need_value "$@"; PUBLIC_URL=${2%/}; shift 2 ;;
    --trusted-proxy) need_value "$@"; TRUSTED_PROXY=$2; shift 2 ;;
    --admin-user) need_value "$@"; ADMIN_USER=$2; shift 2 ;;
    --update) UPDATE_ONLY=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) die "Unknown option: $1 (see --help)" ;;
  esac
done

[[ $EUID -eq 0 ]] || die "Run this script as root."
command -v apt-get >/dev/null || die "This installer supports Debian and Ubuntu (apt-get not found)."
command -v systemctl >/dev/null || die "systemd is required."
[[ -z $PORT || $PORT =~ ^[0-9]+$ && $PORT -ge 1 && $PORT -le 65535 ]] || die "--port must be a number between 1 and 65535."
[[ -z $PUBLIC_URL || $PUBLIC_URL =~ ^https?:// ]] || die "--public-url must start with http:// or https://."
[[ $ADMIN_USER =~ ^[A-Za-z0-9_.@-]{2,64}$ ]] || die "--admin-user may only contain letters, digits and . _ @ -"

FRESH=1
[[ -f $ENV_FILE ]] && FRESH=0
if [[ $UPDATE_ONLY -eq 1 && $FRESH -eq 1 ]]; then
  die "AgentMailSender is not installed here yet; run without --update."
fi

# Remember where the code came from, so updates need no arguments.
SAVED_SOURCE_KIND=""
SAVED_REPO_URL=""
SAVED_REF=""
if [[ -f $INSTALL_CONF ]]; then
  # shellcheck source=/dev/null
  source "$INSTALL_CONF"
fi
if [[ -n $ARCHIVE ]]; then
  SOURCE_KIND=archive
elif [[ -n $LOCAL_DIR ]]; then
  SOURCE_KIND=dir
elif [[ -n $REPO_URL || $SAVED_SOURCE_KIND == git || -z $SAVED_SOURCE_KIND ]]; then
  SOURCE_KIND=git
  REPO_URL=${REPO_URL:-${SAVED_REPO_URL:-$DEFAULT_REPO_URL}}
  REF=${REF:-${SAVED_REF:-$DEFAULT_REF}}
else
  die "This installation came from an uploaded archive. Pass --archive FILE, --src-dir DIR or --repo URL."
fi

# --------------------------------------------------------------------------- packages
msg "Installing system packages"
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
  python3 python3-venv ca-certificates curl git tar >/dev/null
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' ||
  die "Python 3.11 or newer is required (found $(python3 -V 2>&1)). Use Debian 12+ or Ubuntu 24.04+."
ok "$(python3 -V)"

# --------------------------------------------------------------------------- user and folders
if ! id -u "$APP_USER" >/dev/null 2>&1; then
  useradd --system --user-group --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi
install -d -m 0755 "$APP_DIR"
install -d -m 0750 -o "$APP_USER" -g "$APP_USER" "$DATA_DIR"
install -d -m 0750 -o root -g "$APP_USER" "$CONF_DIR"

# --------------------------------------------------------------------------- source code
msg "Fetching the source code"
NEW_SRC="$SRC_DIR.new"
rm -rf "$NEW_SRC"
case $SOURCE_KIND in
  archive)
    [[ -f $ARCHIVE ]] || die "Archive not found: $ARCHIVE"
    mkdir -p "$NEW_SRC"
    tar -xzf "$ARCHIVE" -C "$NEW_SRC"
    VERSION_INFO="archive $(basename "$ARCHIVE")"
    ;;
  dir)
    [[ -d $LOCAL_DIR ]] || die "Directory not found: $LOCAL_DIR"
    mkdir -p "$NEW_SRC"
    tar -C "$LOCAL_DIR" --exclude=.git --exclude=.venv --exclude=data --exclude=__pycache__ \
      --exclude='*.egg-info' --exclude=node_modules -cf - . | tar -xf - -C "$NEW_SRC"
    VERSION_INFO="directory $LOCAL_DIR"
    ;;
  git)
    git clone --quiet --depth 1 --branch "$REF" "$REPO_URL" "$NEW_SRC" ||
      die "Could not clone $REPO_URL ($REF). Is the repository reachable and the branch pushed?"
    VERSION_INFO="$REPO_URL @ $REF ($(git -C "$NEW_SRC" rev-parse --short HEAD))"
    ;;
esac
# Archives created on Windows may contain the project inside a top-level folder.
if [[ ! -f $NEW_SRC/pyproject.toml ]]; then
  inner=$(find "$NEW_SRC" -mindepth 2 -maxdepth 2 -name pyproject.toml -print -quit)
  [[ -n $inner ]] || die "The source has no pyproject.toml: is it the AgentMailSender repository?"
  inner_dir=$(dirname "$inner")
  mv "$inner_dir" "$NEW_SRC.inner" && rm -rf "$NEW_SRC" && mv "$NEW_SRC.inner" "$NEW_SRC"
fi
[[ -d $NEW_SRC/agentmailsender ]] || die "The source has no agentmailsender package."
rm -rf "$SRC_DIR"
mv "$NEW_SRC" "$SRC_DIR"
chown -R root:root "$SRC_DIR"
chmod -R u=rwX,go=rX "$SRC_DIR"
ok "$VERSION_INFO"

# --------------------------------------------------------------------------- virtualenv
msg "Installing AgentMailSender into $VENV_DIR"
if [[ ! -x $VENV_DIR/bin/python ]] || ! "$VENV_DIR/bin/python" -c 'import sys' >/dev/null 2>&1; then
  rm -rf "$VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --quiet --disable-pip-version-check --upgrade pip
"$VENV_DIR/bin/pip" install --quiet --disable-pip-version-check --upgrade "$SRC_DIR"
# Same version number but new code: make sure the package itself is replaced.
"$VENV_DIR/bin/pip" install --quiet --disable-pip-version-check --force-reinstall --no-deps "$SRC_DIR"
APP_VERSION=$("$VENV_DIR/bin/agentmailsender" --version | awk '{print $2}')
ok "agentmailsender $APP_VERSION"

# --------------------------------------------------------------------------- configuration
primary_ip() {
  local ip
  ip=$(hostname -I 2>/dev/null | awk '{print $1}') || true
  echo "${ip:-localhost}"
}

set_env() {
  local key=$1 value=$2 tmp
  tmp=$(mktemp)
  awk -v k="$key" -v v="$value" '
    BEGIN { done = 0 }
    index($0, k "=") == 1 { print k "=" v; done = 1; next }
    { print }
    END { if (!done) print k "=" v }
  ' "$ENV_FILE" >"$tmp"
  cat "$tmp" >"$ENV_FILE"
  rm -f "$tmp"
}

get_env() {
  sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1
}

if [[ $FRESH -eq 1 ]]; then
  msg "Writing $ENV_FILE"
  PORT=${PORT:-8080}
  PUBLIC_URL=${PUBLIC_URL:-http://$(primary_ip):$PORT}
  SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
  umask 027
  cat >"$ENV_FILE" <<EOF
# AgentMailSender configuration. Restart after changes: systemctl restart $SERVICE
# Everything else (profiles, notifications, limits) is configured in the web dashboard.

AMS_DATA_DIR=$DATA_DIR
AMS_HOST=0.0.0.0
AMS_PORT=$PORT

# Address operators open; used for links in notifications (the dashboard setting wins).
AMS_PUBLIC_URL=$PUBLIC_URL

# Encrypts SMTP passwords and webhook secrets stored in the database.
# Keep it with your backups: without it saved credentials cannot be decrypted.
AMS_SECRET_KEY=$SECRET_KEY

# Reverse proxies trusted for X-Forwarded-For / X-Forwarded-Proto (comma-separated, or *).
AMS_FORWARDED_ALLOW_IPS=${TRUSTED_PROXY:-127.0.0.1}

# Session cookie Secure flag: auto (set when the request arrived over HTTPS), true or false.
AMS_COOKIE_SECURE=auto
AMS_SESSION_TTL_HOURS=12
AMS_LOG_LEVEL=info
EOF
  umask 022
else
  msg "Keeping the existing configuration in $ENV_FILE"
  [[ -n $PORT ]] && set_env AMS_PORT "$PORT"
  [[ -n $PUBLIC_URL ]] && set_env AMS_PUBLIC_URL "$PUBLIC_URL"
  [[ -n $TRUSTED_PROXY ]] && set_env AMS_FORWARDED_ALLOW_IPS "$TRUSTED_PROXY"
  PORT=$(get_env AMS_PORT)
  PORT=${PORT:-8080}
fi
chown root:"$APP_USER" "$ENV_FILE"
chmod 0640 "$ENV_FILE"

cat >"$INSTALL_CONF" <<EOF
# Written by install.sh: where updates fetch the code from.
SAVED_SOURCE_KIND=$SOURCE_KIND
SAVED_REPO_URL=${REPO_URL:-}
SAVED_REF=${REF:-}
EOF
chmod 0600 "$INSTALL_CONF"

# --------------------------------------------------------------------------- helper commands
# Keep a copy of the installer for agentmailsender-update: preferably the one shipped with the code
# just installed, so updates also update the installer. Written to a new file and renamed, because
# the running copy may be this very file.
INSTALLER_SOURCE=""
if [[ -f $SRC_DIR/deploy/proxmox/install.sh ]]; then
  INSTALLER_SOURCE=$SRC_DIR/deploy/proxmox/install.sh
elif [[ -n ${BASH_SOURCE[0]:-} && -f ${BASH_SOURCE[0]} ]]; then
  INSTALLER_SOURCE=${BASH_SOURCE[0]}
fi
if [[ -n $INSTALLER_SOURCE ]]; then
  sed 's/\r$//' "$INSTALLER_SOURCE" >"$APP_DIR/install.sh.new"
  chmod 0755 "$APP_DIR/install.sh.new"
  mv -f "$APP_DIR/install.sh.new" "$APP_DIR/install.sh"
elif [[ ! -f $APP_DIR/install.sh ]]; then
  warn "Could not keep a copy of the installer; agentmailsender-update will not work until you run install.sh again from a file."
fi

cat >/usr/local/sbin/agentmailsender-admin <<EOF
#!/usr/bin/env bash
# Run the agentmailsender command line as the service user, with the service configuration.
# Examples: agentmailsender-admin create-operator alice
#           agentmailsender-admin reset-password admin
set -euo pipefail
umask 027
set -a
. $ENV_FILE
set +a
cd $DATA_DIR
exec runuser -u $APP_USER -- $VENV_DIR/bin/agentmailsender "\$@"
EOF
chmod 0755 /usr/local/sbin/agentmailsender-admin

cat >/usr/local/sbin/agentmailsender-update <<EOF
#!/usr/bin/env bash
# Update AgentMailSender from the source it was installed from (options: see --help).
exec bash $APP_DIR/install.sh --update "\$@"
EOF
chmod 0755 /usr/local/sbin/agentmailsender-update

# --------------------------------------------------------------------------- first operator
ADMIN_PASSWORD=""
if [[ ! -f $DATA_DIR/agentmailsender.db ]]; then
  msg "Creating the first operator account"
  ADMIN_PASSWORD=$(python3 -c 'import secrets, string; a = string.ascii_letters + string.digits; print("".join(secrets.choice(a) for _ in range(20)))')
  /usr/local/sbin/agentmailsender-admin create-operator "$ADMIN_USER" --password "$ADMIN_PASSWORD" >/dev/null
  umask 077
  cat >"$CREDENTIALS_FILE" <<EOF
AgentMailSender first sign-in ($(date -u +%Y-%m-%dT%H:%MZ))
URL:      $(get_env AMS_PUBLIC_URL)
Username: $ADMIN_USER
Password: $ADMIN_PASSWORD

Change the password under Settings > Account, then delete this file.
EOF
  umask 022
  ok "Operator '$ADMIN_USER' created"
fi

# --------------------------------------------------------------------------- systemd
msg "Configuring the systemd service"
CAPABILITIES=""
if [[ $PORT -lt 1024 ]]; then
  CAPABILITIES=$'AmbientCapabilities=CAP_NET_BIND_SERVICE\nCapabilityBoundingSet=CAP_NET_BIND_SERVICE'
fi
cat >"$UNIT_FILE" <<EOF
[Unit]
Description=AgentMailSender - human approval gate for agent e-mail
Documentation=https://github.com/KorinBlack/AgentMailSender
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
EnvironmentFile=$ENV_FILE
WorkingDirectory=$DATA_DIR
ExecStart=$VENV_DIR/bin/agentmailsender serve
Restart=on-failure
RestartSec=5
UMask=0027
$CAPABILITIES

[Install]
WantedBy=multi-user.target
EOF

mkdir -p "$DROPIN_DIR"
cat >"$HARDENING_DROPIN" <<EOF
# Sandboxing. Needs mount namespaces: in a Proxmox container enable the "nesting" feature.
# The installer removes this file automatically when the container does not support it.
[Service]
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$DATA_DIR
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictRealtime=true
LockPersonality=true
EOF

wait_healthy() {
  local _
  for _ in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
      return 0
    fi
    if [[ $(systemctl show -p ActiveState --value "$SERVICE") == failed ]] ||
      [[ $(systemctl show -p ExecMainStatus --value "$SERVICE") == 226 ]]; then
      return 1
    fi
    sleep 1
  done
  return 1
}

systemctl daemon-reload
systemctl enable --quiet "$SERVICE"
systemctl reset-failed "$SERVICE" >/dev/null 2>&1 || true
systemctl restart "$SERVICE" || true
if ! wait_healthy; then
  status=$(systemctl show -p ExecMainStatus --value "$SERVICE" || echo "")
  if [[ $status == 226 ]] || journalctl -u "$SERVICE" -n 20 --no-pager 2>/dev/null | grep -q 'NAMESPACE'; then
    warn "This container does not support systemd sandboxing (enable 'nesting' in Proxmox); running without it."
    rm -f "$HARDENING_DROPIN"
    systemctl daemon-reload
    systemctl reset-failed "$SERVICE" >/dev/null 2>&1 || true
    systemctl restart "$SERVICE" || true
  fi
  if ! wait_healthy; then
    journalctl -u "$SERVICE" -n 40 --no-pager >&2 || true
    die "The service did not start. See the log above (journalctl -u $SERVICE)."
  fi
fi
ok "Service running on port $PORT"

# --------------------------------------------------------------------------- summary
URL=$(get_env AMS_PUBLIC_URL)
echo
printf '\033[1;32mAgentMailSender %s is %s.\033[0m\n' "$APP_VERSION" "$([[ $FRESH -eq 1 ]] && echo installed || echo updated)"
echo
echo "  Dashboard:   $URL"
if [[ -n $ADMIN_PASSWORD ]]; then
  echo "  Username:    $ADMIN_USER"
  echo "  Password:    $ADMIN_PASSWORD"
  echo "               (also saved in $CREDENTIALS_FILE: delete it after the first sign-in)"
fi
echo
echo "  Config:      $ENV_FILE"
echo "  Data:        $DATA_DIR (database, attachments)"
echo "  Logs:        journalctl -u $SERVICE -f"
echo "  Update:      agentmailsender-update"
echo "  Operators:   agentmailsender-admin create-operator <name> | reset-password <name>"
echo
if [[ $URL == http://* ]]; then
  echo "  Tip: put the dashboard behind an HTTPS reverse proxy, then run"
  echo "       agentmailsender-update --public-url https://<your-domain> --trusted-proxy <proxy IP>"
  echo
fi
