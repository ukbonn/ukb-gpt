#!/bin/sh
set -e

# ==============================================================================
# UNIFIED SECURITY TOOLS INSTALLER
# ==============================================================================

# --- HELPER FUNCTION: Sanitize Ping ---
# Purpose:
# 1. Finds the ping binary.
# 2. Strips SUID (Set-User-ID) bits.
# 3. Strips File Capabilities (xattrs) by recreating the file node.
#    This forces the binary to use the 'ping_group_range' sysctl (safe/unprivileged)
#    instead of trying to elevate privileges (which gets blocked by no-new-privileges).
sanitize_ping_binary() {
    TARGET_BIN=$(command -v ping 2>/dev/null || true)

    if [ -z "$TARGET_BIN" ]; then
        echo "   ⚠️ Warning: 'ping' binary not found. Skipping sanitization."
        return
    fi

    echo "   >> Sanitizing $TARGET_BIN (Stripping SUID & Capabilities)..."

    # 1. Strip SUID explicitly (failsafe for older binaries)
    chmod u-s "$TARGET_BIN" || true

    # 2. Strip File Capabilities (xattrs) via Copy-Delete-Move
    #    'cp' creates a new file without copying extended attributes (capabilities).
    cp "$TARGET_BIN" /tmp/ping_clean
    rm -f "$TARGET_BIN"
    mv /tmp/ping_clean "$TARGET_BIN"

    # 3. Ensure it is executable
    chmod 755 "$TARGET_BIN"

    if [ -u "$TARGET_BIN" ]; then
        echo "   ⚠️ Warning: SUID bit remains on $TARGET_BIN after sanitization."
    fi
    if command -v getcap >/dev/null 2>&1; then
        REMAINING_CAPS=$(getcap "$TARGET_BIN" 2>/dev/null || true)
        if [ -n "$REMAINING_CAPS" ]; then
            echo "   ⚠️ Warning: File capabilities remain on $TARGET_BIN: $REMAINING_CAPS"
        fi
    fi
}

echo "🔧 [Security Installer] Detecting Package Manager..."

# --- A. INSTALL DEPENDENCIES ---
if command -v apt-get >/dev/null; then
    # Debian/Ubuntu (vLLM, OpenWebUI)
    apt-get update && apt-get install -y --no-install-recommends iputils-ping curl
    
    # Call the helper
    sanitize_ping_binary
    
    # Remove networking tools for hardening
    apt-get remove -y iptables iproute2 || true
    rm -rf /var/lib/apt/lists/*
    
elif command -v microdnf >/dev/null; then
    # UBI/RedHat
    microdnf install -y iputils curl
    
    # Call the helper
    sanitize_ping_binary
    
    microdnf remove -y iptables iproute || true
    microdnf clean all

elif command -v apk >/dev/null; then
    # Alpine (Nginx, Grafana)
    apk add --no-cache iputils curl
    
    # Call the helper
    sanitize_ping_binary
    
    apk del iptables || true

else
    echo "❌ Error: Unknown package manager."
    exit 1
fi

# --- B. INSTALL SCRIPTS ---
SCRIPT_DIR=$(dirname "$0")

echo "🔧 [Security Installer] Installing Supervisor Scripts..."

for script in \
    active_isolation_monitoring_entrypoint.sh \
    healthcheck.sh \
    check_egress.sh \
    nginx_debug_dump.sh
do
    cp "$SCRIPT_DIR/$script" "/usr/local/bin/$script"
    chmod 755 "/usr/local/bin/$script"
done

echo "✅ [Security Installer] Complete."
