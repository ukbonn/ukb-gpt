#!/bin/sh
set -e

# ==============================================================================
# SECURE ENTRYPOINT (SUPERVISOR MODE)
# ------------------------------------------------------------------------------
# 1. Starts the Main Application (Nginx/vLLM/OpenWebUI)
# 2. Runs Active Egress Monitoring (Isolation Fail-Safe)
# 3. Handles Signal Propagation (Graceful Shutdowns)
# 
# NOTE: This script does NOT configure the firewall. 
#       Network isolation is enforced by the Host (start.py + apply_host_firewall.py)
#       and the Docker network topology.
# ==============================================================================

# --- Configuration ---
# Targets to check for unauthorized connectivity (Public DNS)
FORBIDDEN_IPS=${FORBIDDEN_IPS-"8.8.8.8 1.1.1.1 9.9.9.9 2001:4860:4860::8888 2606:4700:4700::1111 2620:fe::fe"}
FORBIDDEN_HTTPS_TARGETS=${FORBIDDEN_HTTPS_TARGETS-"1.1.1.1 2606:4700:4700::1111"}
FORBIDDEN_HTTPS_PORT=${FORBIDDEN_HTTPS_PORT-"443"}

# Check frequency in seconds (Fast detection)
HEARTBEAT_INTERVAL=${HEARTBEAT_INTERVAL:-5} 

# Log frequency (Reduce spam). Log only every Nth successful check.
# 5s * 12 = 60s (Log roughly once a minute)
HEARTBEAT_LOG_MODULO=${HEARTBEAT_LOG_MODULO:-12}

log() {
    echo "$(date '+%H:%M:%S') [SecureEntry] $1"
}

monitor_fail() {
    log "!!! ISOLATION MONITOR FAILURE !!! $1"
    kill -TERM "$APP_PID" 2>/dev/null
    wait "$APP_PID" 2>/dev/null || true
    exit 1
}

preflight_monitor_tools() {
    if [ ! -x /usr/local/bin/check_egress.sh ]; then
        log "Isolation monitor missing /usr/local/bin/check_egress.sh"
        exit 1
    fi

    if [ -n "$FORBIDDEN_IPS" ] && ! command -v ping >/dev/null 2>&1; then
        log "Isolation monitor requires ping for ICMP probes."
        exit 1
    fi

    if [ -n "$FORBIDDEN_HTTPS_TARGETS" ] && ! command -v curl >/dev/null 2>&1; then
        log "Isolation monitor requires curl for HTTPS probes."
        exit 1
    fi
}

run_probe() {
    MODE=$1
    TARGET=$2

    if [ "$MODE" = "https" ]; then
        if /usr/local/bin/check_egress.sh --https --port "$FORBIDDEN_HTTPS_PORT" "$TARGET" > /dev/null 2>&1; then
            RESULT=0
        else
            RESULT=$?
        fi
    else
        if /usr/local/bin/check_egress.sh "$TARGET" > /dev/null 2>&1; then
            RESULT=0
        else
            RESULT=$?
        fi
    fi

    case "$RESULT" in
        0)
            log "!!! SECURITY ALERT !!! Unauthorized ${MODE} egress detected ($TARGET reachable)."
            log "!!! INITIATING EMERGENCY SHUTDOWN !!!"
            kill -9 "$APP_PID" 2>/dev/null
            exit 0
            ;;
        1)
            return 0
            ;;
        *)
            monitor_fail "Probe for ${MODE} target ${TARGET} returned audit error exit ${RESULT}."
            ;;
    esac
}

preflight_monitor_tools

# ==============================================================================
# 1. APPLICATION STARTUP
# ==============================================================================
# We execute the command passed to the container in the background.
# This allows this script (PID 1) to remain running as the Supervisor.
log "Starting Application: $*"
"$@" &
APP_PID=$!
log "Application started with PID: $APP_PID"


# ==============================================================================
# 2. SIGNAL TRAPPING
# ==============================================================================
# If Docker sends a stop signal to us (PID 1), we forward it to the application.
# The 'wait $!' at the bottom of the loop is what makes this responsive.
_term() { 
  log "Caught SIGTERM/SIGINT signal. Initiating graceful shutdown..."
  kill -TERM "$APP_PID" 2>/dev/null
  wait "$APP_PID"
  exit 0
}
trap _term TERM INT


# ==============================================================================
# 3. ACTIVE EGRESS MONITOR (ISOLATION FAIL-SAFE)
# ==============================================================================
log "Starting Active Egress Monitor (Interval: ${HEARTBEAT_INTERVAL}s)..."

ITERATION=0

while true; do
    # A. Liveness Check
    # If the main application crashes (e.g. OOM), we exit with ITS exit code.
    # This preserves the 'restart: on-failure' behavior for genuine app crashes.
    if ! kill -0 "$APP_PID" > /dev/null 2>&1; then
        log "Main application (PID $APP_PID) has exited. Container stopping."
        wait "$APP_PID"
        exit $?
    fi

    # B. Integrity Check (The Fail-Safe)
    # Uses the unified check_egress.sh to verify network isolation.
    # ICMP and HTTPS cover both packet reachability and TCP/TLS path creation.
    for ip in $FORBIDDEN_IPS; do
        run_probe "icmp" "$ip"
    done
    for ip in $FORBIDDEN_HTTPS_TARGETS; do
        run_probe "https" "$ip"
    done

    # C. Audit Heartbeat
    # We use modulo arithmetic to log only occasionally, but ALWAYS log on 
    # the first iteration (0) so automated tests find the string immediately.
    if [ $((ITERATION % HEARTBEAT_LOG_MODULO)) -eq 0 ]; then
        echo "$(date '+%H:%M:%S') [Health::Isolation] Status: PUBLIC DNS UNREACHABLE"
    fi

    ITERATION=$((ITERATION + 1))

    # --- RESPONSIVE SLEEP ---
    # We run sleep in background and wait so signals (SIGTERM) are processed 
    # immediately by the 'trap' defined above. Without this, the container 
    # would take up to $HEARTBEAT_INTERVAL seconds to respond to 'docker stop'.
    sleep "$HEARTBEAT_INTERVAL" &
    wait $!
done
