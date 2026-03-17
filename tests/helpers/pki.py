from pathlib import Path

from tests.helpers.commands import assert_ok, run


def _openssl(args: list[str]):
    return run(["openssl", *args], shell=False)


def create_test_pki(artifacts_dir: Path) -> dict:
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    openssl_config_path = artifacts_dir / "openssl_test.cnf"
    config_content = """[ v3_ca ]
basicConstraints = critical,CA:TRUE
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always,issuer
keyUsage = critical, digitalSignature, cRLSign, keyCertSign

[ v3_leaf ]
basicConstraints = CA:FALSE
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always,issuer
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:localhost, IP:127.0.0.1, IP:172.20.0.6, IP:172.21.0.6, IP:172.30.0.6, IP:172.31.0.6
"""
    openssl_config_path.write_text(config_content, encoding="utf-8")

    root_key = artifacts_dir / "test_root_ca.key"
    root_crt = artifacts_dir / "test_root_ca.crt"
    inter_key = artifacts_dir / "test_intermediate.key"
    inter_csr = artifacts_dir / "test_intermediate.csr"
    inter_crt = artifacts_dir / "test_intermediate.crt"
    server_key = artifacts_dir / "test_server.key"
    server_csr = artifacts_dir / "test_server.csr"
    server_crt = artifacts_dir / "test_server.crt"
    fullchain = artifacts_dir / "test_fullchain.pem"

    assert_ok(
        _openssl(["genrsa", "-out", str(root_key), "2048"]),
        "Failed to generate root CA key",
    )
    assert_ok(
        _openssl(
            [
                "req",
                "-x509",
                "-new",
                "-nodes",
                "-key",
                str(root_key),
                "-sha256",
                "-days",
                "1",
                "-out",
                str(root_crt),
                "-subj",
                "/CN=Test-Root",
                "-config",
                str(openssl_config_path),
                "-extensions",
                "v3_ca",
            ]
        ),
        "Failed to generate root CA cert",
    )

    assert_ok(
        _openssl(["genrsa", "-out", str(inter_key), "2048"]),
        "Failed to generate intermediate CA key",
    )
    assert_ok(
        _openssl(
            [
                "req",
                "-new",
                "-key",
                str(inter_key),
                "-out",
                str(inter_csr),
                "-subj",
                "/CN=Test-Intermediate",
            ]
        ),
        "Failed to generate intermediate CA CSR",
    )
    assert_ok(
        _openssl(
            [
                "x509",
                "-req",
                "-in",
                str(inter_csr),
                "-CA",
                str(root_crt),
                "-CAkey",
                str(root_key),
                "-CAcreateserial",
                "-out",
                str(inter_crt),
                "-days",
                "1",
                "-sha256",
                "-extfile",
                str(openssl_config_path),
                "-extensions",
                "v3_ca",
            ]
        ),
        "Failed to generate intermediate CA cert",
    )

    assert_ok(
        _openssl(["genrsa", "-out", str(server_key), "2048"]),
        "Failed to generate server key",
    )
    assert_ok(
        _openssl(
            [
                "req",
                "-new",
                "-key",
                str(server_key),
                "-out",
                str(server_csr),
                "-subj",
                "/CN=localhost",
            ]
        ),
        "Failed to generate server CSR",
    )
    assert_ok(
        _openssl(
            [
                "x509",
                "-req",
                "-in",
                str(server_csr),
                "-CA",
                str(inter_crt),
                "-CAkey",
                str(inter_key),
                "-CAcreateserial",
                "-out",
                str(server_crt),
                "-days",
                "1",
                "-sha256",
                "-extfile",
                str(openssl_config_path),
                "-extensions",
                "v3_leaf",
            ]
        ),
        "Failed to generate server cert",
    )

    with fullchain.open("wb") as outfile:
        for fname in [server_crt, inter_crt]:
            content = fname.read_bytes()
            outfile.write(content)
            if not content.endswith(b"\n"):
                outfile.write(b"\n")

    # Ensure containers running as non-root can read the TLS materials.
    # These artifacts are test-only and never used in production.
    server_key.chmod(0o644)
    fullchain.chmod(0o644)
    root_crt.chmod(0o644)

    return {
        "root_key": root_key,
        "root_crt": root_crt,
        "inter_key": inter_key,
        "inter_crt": inter_crt,
        "server_key": server_key,
        "server_crt": server_crt,
        "fullchain": fullchain,
    }
