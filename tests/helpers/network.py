import textwrap

from tests.helpers.commands import CmdResult, docker_exec


def _connection_probe_script(host: str, port: int) -> str:
    return textwrap.dedent(
        f"""
        import socket, sys
        try:
            s = socket.create_connection(('{host}', {port}), timeout=2)
        except Exception as e:
            print(f'FAILED: {{e}}')
            sys.exit(1)

        try:
            s.settimeout(1)
            data = s.recv(1)
            if data == b'':
                print('REJECTED: Proxy dropped connection via ACL.')
                sys.exit(3)
            print('CONNECTED: Connection stable.')
            sys.exit(0)
        except socket.timeout:
            print('CONNECTED: Connection established (timeout waiting for data).')
            sys.exit(0)
        except ConnectionResetError:
            print('REJECTED: Connection reset by peer (ACL enforced).')
            sys.exit(3)
        except Exception as e:
            print(f'FAILED: {{e}}')
            sys.exit(1)
        finally:
            s.close()
        """
    ).strip()


def connection_test(src: str, host: str, port: int) -> CmdResult:
    return docker_exec(src, ["python3", "-c", _connection_probe_script(host, port)])
