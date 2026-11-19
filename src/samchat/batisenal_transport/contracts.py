"""Host-owned adapters. Defining these protocols does not grant authority."""

from typing import Protocol

from starlette.requests import Request


class ConnectionResolver(Protocol):
    async def __call__(self, request: Request) -> str:
        """Validate bearer credentials; return a request-local verified reference.

        Never trust actor/scope from body, query, headers other than verified
        credentials, cookies or inherited root. The existing IdentityProvider
        must revalidate that reference on each Bridge operation.
        """
