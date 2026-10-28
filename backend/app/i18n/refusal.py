"""HTTP refusals that carry a message code beside their English sentence.

A refusal's JSON body keeps ``detail`` exactly as it was and gains top-level siblings:
``detail_code`` and ``detail_params`` for a sentence, ``detail_codes`` (a parallel list of
``{code, params}``) for a list of sentences. A ``detail`` that is already an object keeps
its ``message`` and gains ``code`` and ``params`` inside it. One exception handler in
``app/main.py`` builds every such body, so no route hand-builds one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app.i18n.messages import MessageRef, render
from app.security.rbac import PermissionDenied


class CodedHTTPException(HTTPException):
    """An ``HTTPException`` whose sentence is rendered from the message registry.

    Exactly one shape per instance:

    - ``ref``: ``detail`` is the English sentence;
    - ``ref`` and ``fields``: ``detail`` is ``{"message": <sentence>, **fields}`` and the code
      goes inside it (``code``, ``params``), as the typed conflict bodies already carry
      ``message``;
    - ``refs``: ``detail`` is the list of English sentences, one code per item.
    """

    def __init__(
        self,
        status_code: int,
        ref: MessageRef | None = None,
        *,
        refs: Sequence[MessageRef] | None = None,
        fields: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        if (ref is None) == (refs is None):
            raise ValueError("a coded refusal carries one ref or a list of refs")
        if refs is not None and fields is not None:
            raise ValueError("a list of refs has no extra fields")
        self.ref = ref
        self.refs = tuple(refs) if refs is not None else None
        detail: Any
        if refs is not None:
            detail = [render(item) for item in refs]
        elif ref is not None and fields is not None:
            detail = {"message": render(ref), **fields, **ref.to_wire()}
        else:
            assert ref is not None
            detail = render(ref)
        super().__init__(
            status_code=status_code,
            detail=detail,
            headers=dict(headers) if headers is not None else None,
        )

    def body(self) -> dict[str, Any]:
        """The JSON body: ``detail`` as before, plus the code siblings."""
        out: dict[str, Any] = {"detail": self.detail}
        if self.refs is not None:
            out["detail_codes"] = [item.to_wire() for item in self.refs]
        elif self.ref is not None and not isinstance(self.detail, dict):
            wire = self.ref.to_wire()
            out["detail_code"] = wire["code"]
            out["detail_params"] = wire["params"]
        return out


async def coded_http_exception_handler(_request: Any, exc: CodedHTTPException) -> JSONResponse:
    """The response of a coded refusal."""
    return JSONResponse(status_code=exc.status_code, content=exc.body(), headers=exc.headers)


def coded_body(ref: MessageRef) -> dict[str, Any]:
    """A ``{detail, detail_code, detail_params}`` body, for a refusal built outside FastAPI."""
    wire = ref.to_wire()
    return {
        "detail": render(ref),
        "detail_code": wire["code"],
        "detail_params": wire["params"],
    }


def permission_ref(permission: str) -> MessageRef:
    return MessageRef("auth.permission.missing", {"permission": permission})


def permission_refusal(exc: PermissionDenied) -> CodedHTTPException:
    """The 403 of a missing permission; its text is ``str(exc)``, unchanged."""
    return CodedHTTPException(403, permission_ref(exc.permission))
