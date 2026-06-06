import os

from supabase import AsyncClient, create_async_client

_SUPABASE_URL: str = os.environ["SUPABASE_URL"]
_ANON_KEY: str = os.environ["SUPABASE_ANON_KEY"]
# Support both the .env.example key name and the spec alias
_SERVICE_KEY: str = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ["SUPABASE_SERVICE_KEY"]


async def service_client() -> AsyncClient:
    """Service-role client — bypasses RLS. Use only for server-side writes."""
    return await create_async_client(_SUPABASE_URL, _SERVICE_KEY)


async def anon_client(jwt: str) -> AsyncClient:
    """Anon-key client authenticated with a Clerk JWT.

    The JWT sets auth.uid() inside Supabase RLS so user-scoped policies
    (e.g. compliance_rules_select_user_defined) return the correct rows.
    """
    client = await create_async_client(_SUPABASE_URL, _ANON_KEY)
    client.postgrest.auth(jwt)
    return client
