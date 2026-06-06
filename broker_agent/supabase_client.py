import os

from dotenv import load_dotenv
from supabase import AsyncClient, create_async_client

load_dotenv()  # ensure .env is loaded even when this module is imported directly

# Cache created clients so we don't spin up a fresh AsyncClient on every request.
_clients: dict[str, AsyncClient] = {}


def _clean(value: str | None) -> str:
    """Strip surrounding whitespace and quotes that shell/dotenv may leave behind."""
    if not value:
        return ""
    return value.strip().strip('"').strip("'").strip()


def _url() -> str:
    v = _clean(os.environ.get("SUPABASE_URL", ""))
    if not v:
        raise KeyError("SUPABASE_URL is not set")
    return v


def _service_key() -> str:
    key = _clean(
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("SUPABASE_SERVICE_KEY")
    )
    if not key:
        raise KeyError("SUPABASE_SERVICE_ROLE_KEY is not set")
    return key


def _anon_key() -> str:
    key = _clean(os.environ.get("SUPABASE_ANON_KEY", ""))
    if not key:
        raise KeyError("SUPABASE_ANON_KEY is not set")
    return key


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
