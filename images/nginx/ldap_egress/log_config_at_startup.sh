#!/bin/sh
set -e

/usr/local/bin/nginx_debug_dump.sh "LDAP-Config" "/etc/nginx/conf.d/ldap.conf"
