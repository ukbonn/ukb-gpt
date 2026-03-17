import pytest

from tests.helpers.docker import inspect_container

pytestmark = [pytest.mark.isolation, pytest.mark.chatbot_provider, pytest.mark.batch_client]


def _get_bindings(proc_file: str) -> dict:
    bindings = {}
    try:
        with open(proc_file, "r", encoding="utf-8") as handle:
            next(handle)
            for line in handle:
                parts = line.split()
                if len(parts) < 4:
                    continue
                # Only consider listening sockets (TCP_LISTEN == 0A).
                state = parts[3]
                if state != "0A":
                    continue
                local_addr = parts[1]
                ip_hex, port_hex = local_addr.split(":")
                port = int(port_hex, 16)
                bindings[port] = ip_hex
    except FileNotFoundError:
        return {}
    return bindings


def _published_ports(container_name: str) -> dict:
    data = inspect_container(container_name)
    return (data.get("NetworkSettings", {}).get("Ports", {}) or {})


def _has_loopback_binding(published: dict, container_port: int, host_port: int) -> bool:
    bindings = published.get(f"{container_port}/tcp") or []
    return any(
        binding.get("HostIp") == "127.0.0.1" and binding.get("HostPort") == str(host_port)
        for binding in bindings
    )


def test_ports_bound_to_localhost_only(stack):
    # Ensure expected ports bind only to loopback and nothing unintended is exposed.
    ports_not_listening = [3000, 8080, 8090, 9090]
    tcp4 = _get_bindings("/proc/net/tcp")
    tcp6 = _get_bindings("/proc/net/tcp6")
    ingress_published = _published_ports("ukbgpt_ingress")

    ports_local_only = []
    if stack.mode == "chatbot_provider":
        if stack.http_port is not None:
            ports_local_only.append(stack.http_port)
        if stack.https_port is not None:
            ports_local_only.append(stack.https_port)
        if stack.diagnostic_port is not None and stack.diagnostic_port_end is not None:
            ports_local_only.extend(range(stack.diagnostic_port, stack.diagnostic_port_end + 1))
        if stack.ingress_exporter_port is not None:
            ports_local_only.append(stack.ingress_exporter_port)
    elif stack.batch_port is not None:
        ports_local_only.append(stack.batch_port)
        direct_start = int(stack.env.get("BATCH_CLIENT_DIRECT_PORT_START", "30001"))
        direct_end = int(stack.env.get("BATCH_CLIENT_DIRECT_PORT_END", "30032"))
        ports_local_only.extend(range(direct_start, direct_end + 1))
        ports_local_only.extend(range(5000, 5008))
        ports_local_only.append(8001)

    failures = []
    for port in ports_local_only:
        found = False

        if port in tcp4:
            found = True
            ip_hex = tcp4[port]
            if ip_hex == "0100007F":
                pass
            elif ip_hex == "00000000":
                failures.append(f"Port {port} exposed on 0.0.0.0")
            else:
                failures.append(f"Port {port} exposed on IPv4 {ip_hex}")

        if port in tcp6:
            ip_hex = tcp6[port]
            if ip_hex == "00000000000000000000000001000000":
                found = True
            elif ip_hex == "00000000000000000000000000000000":
                failures.append(f"Port {port} exposed on ::")
                found = True
            elif not found:
                failures.append(f"Port {port} exposed on IPv6 {ip_hex}")
                found = True

        if not found:
            pass

    for port in ports_not_listening:
        if port in tcp4 or port in tcp6:
            failures.append(f"Port {port} should not be bound on host")

    if stack.mode == "chatbot_provider":
        if stack.http_port is not None and not _has_loopback_binding(
            ingress_published, 80, stack.http_port
        ):
            failures.append(
                f"Ingress container port 80 is not published to 127.0.0.1:{stack.http_port}"
            )
        if stack.https_port is not None and not _has_loopback_binding(
            ingress_published, 443, stack.https_port
        ):
            failures.append(
                f"Ingress container port 443 is not published to 127.0.0.1:{stack.https_port}"
            )
        if stack.diagnostic_port is not None and stack.diagnostic_port_end is not None:
            for offset, host_port in enumerate(
                range(stack.diagnostic_port, stack.diagnostic_port_end + 1)
            ):
                container_port = 5000 + offset
                if not _has_loopback_binding(ingress_published, container_port, host_port):
                    failures.append(
                        "Ingress diagnostic tunnel container port "
                        f"{container_port} is not published to 127.0.0.1:{host_port}"
                    )
        if stack.ingress_exporter_port is not None and not _has_loopback_binding(
            ingress_published, 8001, stack.ingress_exporter_port
        ):
            failures.append(
                "Ingress exporter tunnel is not published to "
                f"127.0.0.1:{stack.ingress_exporter_port}"
            )
        if 30000 in tcp4 or 30000 in tcp6:
            failures.append("Batch client port 30000 is bound during chatbot provider mode")
        if ingress_published.get("80/tcp"):
            host_ports = {binding.get("HostPort") for binding in ingress_published["80/tcp"] or []}
            if "80" in host_ports:
                failures.append("Ingress container port 80 is still published on host port 80")
        if ingress_published.get("443/tcp"):
            host_ports = {binding.get("HostPort") for binding in ingress_published["443/tcp"] or []}
            if "443" in host_ports:
                failures.append("Ingress container port 443 is still published on host port 443")
    else:
        if stack.batch_port is not None and not _has_loopback_binding(
            ingress_published, stack.batch_port, stack.batch_port
        ):
            failures.append(
                f"Batch ingress port {stack.batch_port} is not published to 127.0.0.1:{stack.batch_port}"
            )
        direct_start = int(stack.env.get("BATCH_CLIENT_DIRECT_PORT_START", "30001"))
        direct_end = int(stack.env.get("BATCH_CLIENT_DIRECT_PORT_END", "30032"))
        for port in range(direct_start, direct_end + 1):
            if not _has_loopback_binding(ingress_published, port, port):
                failures.append(
                    f"Batch direct worker port {port} is not published to 127.0.0.1:{port}"
                )
        for port in range(5000, 5008):
            if not _has_loopback_binding(ingress_published, port, port):
                failures.append(
                    f"Batch diagnostic tunnel port {port} is not published to 127.0.0.1:{port}"
                )
        if not _has_loopback_binding(ingress_published, 8001, 8001):
            failures.append("Batch exporter tunnel is not published to 127.0.0.1:8001")
        if 30000 in tcp4:
            ip_hex = tcp4[30000]
            if ip_hex != "0100007F":
                failures.append("Batch port 30000 is not bound to localhost (IPv4)")
        if 30000 in tcp6:
            ip_hex = tcp6[30000]
            if ip_hex != "00000000000000000000000001000000":
                failures.append("Batch port 30000 is not bound to localhost (::1)")
        if ingress_published.get("80/tcp") or ingress_published.get("443/tcp"):
            failures.append("Batch mode ingress should not publish port 80/443")

    assert not failures, "\n".join(failures)
