"""End-to-end delivery through the SMTP transport, against a tiny in-process SMTP server."""

from __future__ import annotations

import socket
import threading

from conftest import make_profile, submit


class FakeSMTPServer(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.messages: list[dict] = []

    def run(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn, conn.makefile("rb") as reader:
                self._session(conn, reader)

    def _session(self, conn, reader) -> None:
        def reply(line: str) -> None:
            conn.sendall(line.encode() + b"\r\n")

        reply("220 fake.local ESMTP")
        envelope = {"from": None, "rcpt": [], "data": b""}
        while True:
            line = reader.readline()
            if not line:
                return
            command = line.decode().strip()
            verb = command.split(" ")[0].upper()
            if verb in {"EHLO", "HELO"}:
                reply("250-fake.local")
                reply("250 8BITMIME")
            elif verb == "MAIL":
                envelope = {"from": command.split(":", 1)[1].strip(" <>"), "rcpt": [], "data": b""}
                reply("250 OK")
            elif verb == "RCPT":
                address = command.split(":", 1)[1].strip(" <>")
                if address.startswith("refused"):
                    reply("550 No such user")
                else:
                    envelope["rcpt"].append(address)
                    reply("250 OK")
            elif verb == "DATA":
                reply("354 End data with <CR><LF>.<CR><LF>")
                chunks = []
                while True:
                    data_line = reader.readline()
                    if data_line in (b".\r\n", b""):
                        break
                    chunks.append(data_line)
                envelope["data"] = b"".join(chunks)
                self.messages.append(envelope)
                reply("250 Queued")
            elif verb == "NOOP" or verb == "RSET":
                reply("250 OK")
            elif verb == "QUIT":
                reply("221 Bye")
                return
            else:
                reply("502 Not implemented")

    def stop(self) -> None:
        self.sock.close()


def test_smtp_delivery_with_bcc_and_partial_refusal(client, admin):
    server = FakeSMTPServer()
    server.start()
    try:
        agent = make_profile(
            admin, name="smtp-bot", transport="smtp",
            config={"host": "127.0.0.1", "port": server.port, "security": "none", "timeout": 5},
        )
        test = admin.post(f"/api/admin/profiles/{agent.id}/test").json()
        assert test["ok"] is True, test

        request_id = submit(client, agent, bcc=["hidden@example.org", "refused@example.org"]).json()["id"]
        result = admin.post(f"/api/admin/requests/{request_id}/approve", json={}).json()
        assert result["status"] == "sent", result
        assert "Recipient refused: refused@example.org" in result["events"][-1]["message"]

        [message] = server.messages
        assert message["from"] == "smtp-bot@example.com"
        assert message["rcpt"] == ["jane@example.org", "hidden@example.org"]
        assert b"Subject: Quote for order 1042" in message["data"]
        assert b"hidden@example.org" not in message["data"]
    finally:
        server.stop()
