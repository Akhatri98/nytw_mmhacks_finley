import os

from supabase import AsyncClient, create_async_client


def _url() -> str:
    return os.environ["SUPABASE_URL"]


def _service_key() -> str:
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY")
    if not key:
        raise KeyError("SUPABASE_SERVICE_ROLE_KEY is not set")
    return key


def _anon_key() -> str:
    return os.environ["SUPABASE_ANON_KEY"]


async def service_client() -> AsyncClient:
    """Service-role client — bypasses RLS. Use only for server-side writes."""
    return await create_async_client(_url(), _service_key())


async def anon_client(jwt: str) -> AsyncClient:
    """Anon-key client authenticated with a Clerk JWT.

    The JWT sets auth.uid() inside Supabase RLS so user-scoped policies
    (e.g. compliance_rules_select_user_defined) return the correct rows.
    """
    client = await create_async_client(_url(), _anon_key())
    client.postgrest.auth(jwt)
    return client
