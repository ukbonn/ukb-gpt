#!/usr/bin/env python3
"""Generate persistent localhost-only test PKI for manual stack startup."""

import argparse
import ipaddress
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List


def run(args: List[str]) -> None:
    result = subprocess.run(args, text=True, capture_output=True)
    if result.returncode != 0:
        cmd = " ".join(shlex.quote(part) for part in args)
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        detail = "\n".join(line for line in [stdout, stderr] if line)
        raise RuntimeError(f"Command failed: {cmd}\n{detail}")


def validate_ips(values: Iterable[str]) -> List[str]:
    ips: List[str] = []
    for raw in values:
        try:
            ipaddress.ip_address(raw)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"Invalid IP address: {raw}") from exc
        ips.append(raw)
    return ips


def build_openssl_config(path: Path, san_dns: List[str], san_ips: List[str]) -> None:
    alt_names: List[str] = []
    for idx, dns_name in enumerate(san_dns, start=1):
        alt_names.append(f"DNS.{idx} = {dns_name}")
    for idx, ip in enumerate(san_ips, start=1):
        alt_names.append(f"IP.{idx} = {ip}")

    config = "\n".join(
        [
            "[ v3_ca ]",
            "basicConstraints = critical,CA:TRUE",
            "subjectKeyIdentifier = hash",
            "authorityKeyIdentifier = keyid:always,issuer",
            "keyUsage = critical, digitalSignature, cRLSign, keyCertSign",
            "",
            "[ v3_leaf ]",
            "basicConstraints = CA:FALSE",
            "subjectKeyIdentifier = hash",
            "authorityKeyIdentifier = keyid:always,issuer",
            "keyUsage = critical, digitalSignature, keyEncipherment",
            "extendedKeyUsage = serverAuth",
            "subjectAltName = @alt_names",
            "",
            "[ alt_names ]",
            *alt_names,
            "",
        ]
    )
    path.write_text(config, encoding="utf-8")


def write_env_script(path: Path, server_name: str, fullchain: Path, root_crt: Path, server_key: Path) -> None:
    script = "\n".join(
        [
            "#!/usr/bin/env bash",
            "# shellcheck shell=bash",
            f"export SERVER_NAME={shlex.quote(server_name)}",
            f"export SSL_CERT_PATH={shlex.quote(str(fullchain))}",
            f"export ROOT_CA_PATH={shlex.quote(str(root_crt))}",
            f"export CERTIFICATE_KEY=\"$(cat {shlex.quote(str(server_key))})\"",
            "",
        ]
    )
    path.write_text(script, encoding="utf-8")
    path.chmod(0o700)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate self-signed localhost PKI for manual UKB-GPT startup"
    )
    parser.add_argument(
        "--out-dir",
        default=str(Path.home() / ".ukbgpt-localhost-pki"),
        help="Directory for generated files (default: %(default)s)",
    )
    parser.add_argument(
        "--server-name",
        default="localhost",
        help="Hostname used as cert CN and SERVER_NAME (default: %(default)s)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Certificate lifetime in days (default: %(default)s)",
    )
    parser.add_argument(
        "--san-dns",
        action="append",
        default=[],
        help="Additional DNS SAN (repeatable)",
    )
    parser.add_argument(
        "--san-ip",
        action="append",
        default=[],
        help="Additional IP SAN (repeatable)",
    )

    args = parser.parse_args()

    if args.days < 1:
        parser.error("--days must be >= 1")

    san_dns = ["localhost", args.server_name, *args.san_dns]
    # Preserve order while deduplicating
    san_dns = list(dict.fromkeys(name for name in san_dns if name.strip()))

    san_ips = ["127.0.0.1", *args.san_ip]
    san_ips = validate_ips(list(dict.fromkeys(san_ips)))

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    openssl_cnf = out_dir / "openssl_localhost.cnf"
    root_key = out_dir / "root_ca.key"
    root_crt = out_dir / "root_ca.crt"
    root_serial = out_dir / "root_ca.srl"

    inter_key = out_dir / "intermediate_ca.key"
    inter_csr = out_dir / "intermediate_ca.csr"
    inter_crt = out_dir / "intermediate_ca.crt"
    inter_serial = out_dir / "intermediate_ca.srl"

    server_key = out_dir / "server.key"
    server_csr = out_dir / "server.csr"
    server_crt = out_dir / "server.crt"
    fullchain = out_dir / "fullchain.pem"
    env_script = out_dir / "env.localhost.sh"

    build_openssl_config(openssl_cnf, san_dns, san_ips)

    try:
        run(["openssl", "genrsa", "-out", str(root_key), "2048"])
        run(
            [
                "openssl",
                "req",
                "-x509",
                "-new",
                "-nodes",
                "-key",
                str(root_key),
                "-sha256",
                "-days",
                str(args.days),
                "-out",
                str(root_crt),
                "-subj",
                "/CN=UKBGPT-Localhost-Root",
                "-config",
                str(openssl_cnf),
                "-extensions",
                "v3_ca",
            ]
        )

        run(["openssl", "genrsa", "-out", str(inter_key), "2048"])
        run(
            [
                "openssl",
                "req",
                "-new",
                "-key",
                str(inter_key),
                "-out",
                str(inter_csr),
                "-subj",
                "/CN=UKBGPT-Localhost-Intermediate",
            ]
        )
        run(
            [
                "openssl",
                "x509",
                "-req",
                "-in",
                str(inter_csr),
                "-CA",
                str(root_crt),
                "-CAkey",
                str(root_key),
                "-CAcreateserial",
                "-CAserial",
                str(root_serial),
                "-out",
                str(inter_crt),
                "-days",
                str(args.days),
                "-sha256",
                "-extfile",
                str(openssl_cnf),
                "-extensions",
                "v3_ca",
            ]
        )

        run(["openssl", "genrsa", "-out", str(server_key), "2048"])
        run(
            [
                "openssl",
                "req",
                "-new",
                "-key",
                str(server_key),
                "-out",
                str(server_csr),
                "-subj",
                f"/CN={args.server_name}",
            ]
        )
        run(
            [
                "openssl",
                "x509",
                "-req",
                "-in",
                str(server_csr),
                "-CA",
                str(inter_crt),
                "-CAkey",
                str(inter_key),
                "-CAcreateserial",
                "-CAserial",
                str(inter_serial),
                "-out",
                str(server_crt),
                "-days",
                str(args.days),
                "-sha256",
                "-extfile",
                str(openssl_cnf),
                "-extensions",
                "v3_leaf",
            ]
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    fullchain.write_bytes(server_crt.read_bytes() + b"\n" + inter_crt.read_bytes() + b"\n")

    # Private keys stay user-only; certs are world-readable so containers can mount them.
    root_key.chmod(0o600)
    inter_key.chmod(0o600)
    server_key.chmod(0o600)

    root_crt.chmod(0o644)
    inter_crt.chmod(0o644)
    server_crt.chmod(0o644)
    fullchain.chmod(0o644)

    write_env_script(env_script, args.server_name, fullchain, root_crt, server_key)

    print("Generated localhost PKI:")
    print(f"  Output dir:      {out_dir}")
    print(f"  Full chain PEM:  {fullchain}")
    print(f"  Server key:      {server_key}")
    print(f"  Root CA:         {root_crt}")
    print(f"  Env helper:      {env_script}")
    print()
    print("Manual startup:")
    print(f"  source {shlex.quote(str(env_script))}")
    print("  export WEBUI_SECRET_KEY=\"$(openssl rand -hex 32)\"")
    print("  python3 start.py")
    print()
    print("Optional browser trust (Linux):")
    print(
        "  sudo cp "
        f"{shlex.quote(str(root_crt))} /usr/local/share/ca-certificates/ukbgpt-localhost-root.crt"
    )
    print("  sudo update-ca-certificates")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
