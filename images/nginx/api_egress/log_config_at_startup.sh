#!/bin/sh
set -e

/usr/local/bin/nginx_debug_dump.sh "API-Egress-Config" "/etc/nginx/conf.d/api.conf"
