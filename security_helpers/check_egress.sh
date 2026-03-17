#!/bin/sh
# ==============================================================================
# UNIFIED EGRESS VERIFIER
# ------------------------------------------------------------------------------
# Returns:
#   0 - REACHABLE: Potential Data Exfiltration detected! (Security Breach)
#   1 - BLOCKED:   Confirmed network isolation (Expected).
#   2 - ERROR:     Audit check failed (e.g., Permission Denied, Tool Missing).
# ==============================================================================

VERBOSE=false
TARGET=""
MODE="icmp"
PORT="443"

usage() {
    echo "Usage: $0 [-v] [--icmp | --https] [--port <tcp_port>] <ip_or_domain>"
}

run_icmp_probe() {
    if ! command -v ping >/dev/null 2>&1; then
        echo "[Audit Error] ping is required for ICMP mode." >&2
        exit 2
    fi

    if [ "$VERBOSE" = "true" ]; then
        echo "[Audit-Check] ping -n -c 1 -W 1 \"$TARGET\""
    fi
    OUTPUT=$(ping -n -c 1 -W 1 "$TARGET" 2>&1)
    EXIT_CODE=$?

    if [ $EXIT_CODE -eq 0 ]; then
        # Always print output on breach, regardless of verbose flag
        echo "$OUTPUT"
        exit 0
    fi

    case "$OUTPUT" in
        # Valid isolation responses for ICMP probe.
        *"100% packet loss"*|*"unreachable"*|*"Unreachable"*|*"Address family for hostname not supported"*|*"Protocol not supported"*|*"Cannot assign requested address"*)
            if [ "$VERBOSE" = "true" ]; then
                echo "$OUTPUT"
            fi
            exit 1
            ;;
        *)
            if [ "$VERBOSE" = "true" ]; then
                echo "$OUTPUT"
            fi
            echo "[Audit Error] Unexpected ICMP probe output format." >&2
            exit 2
            ;;
    esac
}

run_https_probe() {
    if ! command -v curl >/dev/null 2>&1; then
        echo "[Audit Error] curl is required for --https mode." >&2
        exit 2
    fi

    case "$PORT" in
        ''|*[!0-9]*)
            echo "[Audit Error] --port must be numeric." >&2
            exit 2
            ;;
    esac

    HOST="$TARGET"
    # URL bracket notation is required for IPv6 literals.
    case "$TARGET" in
        *:*) HOST="[$TARGET]" ;;
    esac
    URL="https://${HOST}:${PORT}/"

    if [ "$VERBOSE" = "true" ]; then
        echo "[Audit-Check] curl -k -sS --connect-timeout 3 --max-time 8 -o /dev/null -w %{http_code} \"$URL\""
    fi
    OUTPUT=$(curl -k -sS --connect-timeout 3 --max-time 8 -o /dev/null -w "%{http_code}" "$URL" 2>&1)
    EXIT_CODE=$?

    if [ $EXIT_CODE -eq 0 ]; then
        # HTTPS/TCP path is reachable; this is a breach in isolated mode.
        echo "$OUTPUT"
        exit 0
    fi

    case "$OUTPUT" in
        *"Could not resolve host"*|*"Network is unreachable"*|*"No route to host"*|*"timed out"*|*"Operation timed out"*|*"Timeout was reached"*|*"Address family for hostname not supported"*|*"Cannot assign requested address"*|*"Could not connect to server"*|*"Couldn't connect to server"*)
            if [ "$VERBOSE" = "true" ]; then
                echo "$OUTPUT"
            fi
            exit 1
            ;;
        *"Connection refused"*)
            echo "$OUTPUT"
            exit 0
            ;;
    esac

    case "$EXIT_CODE" in
        # DNS and timeout failures are expected when egress is blocked.
        6|7|28)
            if [ "$VERBOSE" = "true" ]; then
                echo "$OUTPUT"
            fi
            exit 1
            ;;
        # TCP/TLS/session failures can still indicate a completed path.
        35|52|56)
            echo "$OUTPUT"
            exit 0
            ;;
        *)
            if [ "$VERBOSE" = "true" ]; then
                echo "$OUTPUT"
            fi
            echo "[Audit Error] Unexpected HTTPS probe output format." >&2
            exit 2
            ;;
    esac
}

# --- Argument Parsing ---
while [ "$1" != "" ]; do
    case $1 in
        -v | --verbose ) VERBOSE=true ;;
        --icmp )         MODE="icmp" ;;
        --https )        MODE="https" ;;
        --port )
            shift
            if [ -z "$1" ]; then
                echo "[Audit Error] Missing value for --port." >&2
                usage >&2
                exit 2
            fi
            PORT=$1
            ;;
        -h | --help )
            usage
            exit 0
            ;;
        -* )
            echo "[Audit Error] Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
        * )
            if [ -n "$TARGET" ]; then
                echo "[Audit Error] Multiple targets provided: '$TARGET' and '$1'." >&2
                usage >&2
                exit 2
            fi
            TARGET=$1
            ;;
    esac
    shift
done

if [ -z "$TARGET" ]; then
    usage >&2
    exit 2
fi

case "$MODE" in
    icmp) run_icmp_probe ;;
    https) run_https_probe ;;
    *)
        echo "[Audit Error] Unsupported mode: $MODE" >&2
        exit 2
        ;;
esac
