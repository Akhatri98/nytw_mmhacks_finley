import os

from supabase import AsyncClient, create_async_client

# Cache created clients so we don't spin up a fresh AsyncClient on every request.
# Keyed by the API key used. This matters under Playwright's async event loop,
# where repeated client creation can leak connections / loop bindings.
_clients: dict[str, AsyncClient] = {}


def _url() -> str:
    return os.environ["SUPABASE_URL"]


def _service_key() -> str:
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY")
    if not key:
        raise KeyError("SUPABASE_SERVICE_ROLE_KEY is not set")
    return key


def _anon_key() -> str:
    return os.environ["SUPABASE_ANON_KEY"]


async def _get_or_create(key: str) -> AsyncClient:
    """Return a cached AsyncClient for ``key``, creating it once on first use."""
    client = _clients.get(key)
    if client is None:
        client = await create_async_client(_url(), key)
        _clients.setdefault(key, client)
    return _clients[key]


async def service_client() -> AsyncClient:
    """Service-role client — bypasses RLS. Use only for server-side writes."""
    return await _get_or_create(_service_key())


async def anon_client(jwt: str) -> AsyncClient:
    """Anon-key client authenticated with a Clerk JWT.

    The JWT sets auth.uid() inside Supabase RLS so user-scoped policies
    (e.g. compliance_rules_select_user_defined) return the correct rows.

    In demo/test mode the JWT may be empty; in that case we skip the auth call
    and the caller should rely on the service client instead.
    """
    client = await _get_or_create(_anon_key())
    if jwt:
        client.postgrest.auth(jwt)
    return client
