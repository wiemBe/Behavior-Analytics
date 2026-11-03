"""A socket-level SMTP server on localhost for the e-mail tests (MAIL-1).

``aiosmtpd`` is not installed, so this speaks the small part of SMTP that
``smtplib`` needs: greeting, EHLO (no STARTTLS offered), MAIL, RCPT, DATA, RSET,
NOOP, QUIT. It binds 127.0.0.1 on a free port, so nothing leaves the machine.
"""

from __future__ import annotations

import socketserver
import threading
from dataclasses import dataclass, field
from email import message_from_bytes, policy
from email.message import EmailMessage


@dataclass
class Received:
    mail_from: str
    recipients: list[str]
    raw: bytes

    def parsed(self) -> EmailMessage:
        message = message_from_bytes(self.raw, policy=policy.default)
        assert isinstance(message, EmailMessage)
        return message


@dataclass
class FakeSmtpState:
    messages: list[Received] = field(default_factory=list)
    refuse_rcpt: bool = False


class _Handler(socketserver.StreamRequestHandler):
    def _reply(self, line: str) -> None:
        self.wfile.write(line.encode() + b"\r\n")
        self.wfile.flush()

    def handle(self) -> None:
        state: FakeSmtpState = self.server.state  # type: ignore[attr-defined]
        self._reply("220 fake.localhost ESMTP ready")
        mail_from = ""
        recipients: list[str] = []
        while True:
            line = self.rfile.readline()
            if not line:
                return
            command = line.decode(errors="replace").strip()
            verb = command.split(" ", 1)[0].upper()
            if verb in ("EHLO", "HELO"):
                self._reply("250-fake.localhost")
                self._reply("250 8BITMIME")
            elif verb == "MAIL":
                mail_from = command.split(":", 1)[1].strip().strip("<>")
                recipients = []
                self._reply("250 ok")
            elif verb == "RCPT":
                if state.refuse_rcpt:
                    self._reply("550 relay banner: no such mailbox")
                else:
                    recipients.append(command.split(":", 1)[1].strip().strip("<>"))
                    self._reply("250 ok")
            elif verb == "DATA":
                self._reply("354 end with <CRLF>.<CRLF>")
                chunks: list[bytes] = []
                while True:
                    data_line = self.rfile.readline()
                    if data_line in (b".\r\n", b""):
                        break
                    chunks.append(data_line[1:] if data_line.startswith(b"..") else data_line)
                state.messages.append(Received(mail_from, list(recipients), b"".join(chunks)))
                self._reply("250 queued")
            elif verb in ("RSET", "NOOP"):
                self._reply("250 ok")
            elif verb == "QUIT":
                self._reply("221 bye")
                return
            else:
                self._reply("502 not implemented")


class FakeSmtpServer:
    def __init__(self) -> None:
        self.state = FakeSmtpState()
        self._server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        self._server.state = self.state  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def __enter__(self) -> FakeSmtpServer:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
