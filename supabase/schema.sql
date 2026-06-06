-- ============================================================
-- Finley — AI-native agentic trading platform
-- Database initialization script
--
-- Auth:  Clerk handles identity. auth.uid() returns the Clerk
--        `sub` claim (text). All user FKs are TEXT, not UUID.
-- RLS:   Every user-scoped table enforces row-level security.
--        No application-level filtering is required or relied on.
--
-- Safe to re-run: CREATE TABLE IF NOT EXISTS + ON CONFLICT DO NOTHING
-- ============================================================

-- ============================================================
-- EXTENSIONS
-- ============================================================
CREATE EXTENSION IF NOT EXISTS "pgcrypto";


-- ============================================================
-- USERS
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    id                      uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    clerk_user_id           text        UNIQUE NOT NULL,
    phone_number            text        NOT NULL,
    preferred_broker        text,
    risk_tolerance          text        CHECK (risk_tolerance IN ('conservative', 'moderate', 'aggressive')),
    max_single_position_pct numeric(5,2) NOT NULL DEFAULT 20.00,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE users ENABLE ROW LEVEL SECURITY;

CREATE POLICY "users_select_own"
    ON users FOR SELECT
    USING (clerk_user_id = auth.uid()::text);

CREATE POLICY "users_update_own"
    ON users FOR UPDATE
    USING (clerk_user_id = auth.uid()::text)
    WITH CHECK (clerk_user_id = auth.uid()::text);

-- Keep updated_at current automatically
CREATE OR REPLACE FUNCTION touch_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS users_touch_updated_at ON users;
CREATE TRIGGER users_touch_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();


-- ============================================================
-- TRADES  (append-only audit log — no UPDATE or DELETE)
-- ============================================================
CREATE TABLE IF NOT EXISTS trades (
    id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           text        NOT NULL REFERENCES users(clerk_user_id),
    ticker            text        NOT NULL,
    direction         text        NOT NULL CHECK (direction IN ('buy', 'sell', 'short', 'cover')),
    quantity          numeric(18,6) NOT NULL,
    price_executed    numeric(18,4) NOT NULL,
    total_value       numeric(18,4) GENERATED ALWAYS AS (quantity * price_executed) STORED,
    broker            text        NOT NULL,
    status            text        NOT NULL CHECK (status IN ('executed', 'partial', 'failed', 'cancelled')),
    screenshot_url    text        NOT NULL,
    trigger_signal_id text,
    notes             text,
    executed_at       timestamptz NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE trades ENABLE ROW LEVEL SECURITY;

CREATE POLICY "trades_select_own"
    ON trades FOR SELECT
    USING (user_id = auth.uid()::text);

CREATE POLICY "trades_insert_own"
    ON trades FOR INSERT
    WITH CHECK (user_id = auth.uid()::text);

-- Fast per-user recency queries
CREATE INDEX IF NOT EXISTS trades_user_executed_idx
    ON trades (user_id, executed_at DESC);


-- ============================================================
-- COMPLIANCE RULES
-- ============================================================
CREATE TABLE IF NOT EXISTS compliance_rules (
    id                    uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id               text,       -- NULL for standard rules; Clerk user_id for user-defined
    scope                 text        NOT NULL CHECK (scope IN ('standard', 'user_defined')),
    rule_category         text        NOT NULL
        CHECK (rule_category IN ('wash_sale', 'position_limit', 'restricted_asset', 'day_trade_limit', 'custom')),
    jurisdiction          text        NOT NULL,
    rule_text             text        NOT NULL,
    severity              text        NOT NULL CHECK (severity IN ('hard_block', 'soft_warn', 'info')),
    applies_to_tickers    text[]      NOT NULL DEFAULT '{}',
    applies_to_direction  text        CHECK (applies_to_direction IN ('buy', 'sell', 'both')),
    max_position_pct      numeric(5,2),
    cooldown_days         int,
    active                boolean     NOT NULL DEFAULT true,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE compliance_rules ENABLE ROW LEVEL SECURITY;

DROP TRIGGER IF EXISTS compliance_rules_touch_updated_at ON compliance_rules;
CREATE TRIGGER compliance_rules_touch_updated_at
    BEFORE UPDATE ON compliance_rules
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- All authenticated users can read standard rules
CREATE POLICY "compliance_rules_select_standard"
    ON compliance_rules FOR SELECT
    USING (scope = 'standard');

-- Users can read their own user-defined rules
CREATE POLICY "compliance_rules_select_user_defined"
    ON compliance_rules FOR SELECT
    USING (scope = 'user_defined' AND user_id = auth.uid()::text);

-- Users can insert their own user-defined rules
CREATE POLICY "compliance_rules_insert_user_defined"
    ON compliance_rules FOR INSERT
    WITH CHECK (scope = 'user_defined' AND user_id = auth.uid()::text);

-- Users can update their own user-defined rules
CREATE POLICY "compliance_rules_update_user_defined"
    ON compliance_rules FOR UPDATE
    USING (scope = 'user_defined' AND user_id = auth.uid()::text)
    WITH CHECK (scope = 'user_defined' AND user_id = auth.uid()::text);

-- Users can delete their own user-defined rules
CREATE POLICY "compliance_rules_delete_user_defined"
    ON compliance_rules FOR DELETE
    USING (scope = 'user_defined' AND user_id = auth.uid()::text);

-- NOTE: No INSERT/UPDATE/DELETE policy exists for scope='standard'.
-- Only the service role (seed script) can write standard rules.


-- ============================================================
-- ALERTS
-- ============================================================
CREATE TABLE IF NOT EXISTS alerts (
    id                 uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id            text        NOT NULL,
    ticker             text        NOT NULL,
    alert_type         text        NOT NULL CHECK (alert_type IN ('spike', 'dip', 'pick', 'custom')),
    message_sent       text        NOT NULL,
    signal_id          text,
    acted_on           boolean     NOT NULL DEFAULT false,
    resulting_trade_id uuid        REFERENCES trades(id),
    sent_at            timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE alerts ENABLE ROW LEVEL SECURITY;

CREATE POLICY "alerts_select_own"
    ON alerts FOR SELECT
    USING (user_id = auth.uid()::text);

CREATE POLICY "alerts_insert_own"
    ON alerts FOR INSERT
    WITH CHECK (user_id = auth.uid()::text);

CREATE POLICY "alerts_update_own"
    ON alerts FOR UPDATE
    USING (user_id = auth.uid()::text)
    WITH CHECK (user_id = auth.uid()::text);


-- ============================================================
-- SEED DATA — standard compliance rules
-- requires service role / seed script
-- ============================================================
DO $$
BEGIN
    INSERT INTO compliance_rules
        (scope, user_id, rule_category, jurisdiction, severity, rule_text, applies_to_tickers, cooldown_days)
    VALUES
        -- wash_sale_30d
        (
            'standard', NULL,
            'wash_sale', 'US-IRS', 'hard_block',
            'Cannot repurchase the same or substantially identical security within 30 calendar days before or after selling it at a loss.',
            '{}', 30
        ),
        -- pdt_rule
        (
            'standard', NULL,
            'day_trade_limit', 'US-FINRA', 'hard_block',
            'Pattern Day Trader rule: executing more than 3 day trades within a rolling 5 business-day window requires maintaining at least $25,000 in account equity.',
            '{}', NULL
        ),
        -- reg_t_margin
        (
            'standard', NULL,
            'position_limit', 'US-SEC', 'hard_block',
            'Regulation T limits margin credit to 50% of the security purchase price at time of transaction.',
            '{}', NULL
        ),
        -- sec_restricted
        (
            'standard', NULL,
            'restricted_asset', 'US-SEC', 'hard_block',
            'Trading in securities subject to a regulatory halt or suspension is prohibited.',
            '{}', NULL
        ),
        -- finra_suitability
        (
            'standard', NULL,
            'custom', 'US-FINRA', 'soft_warn',
            'FINRA Rule 2111: any recommendation must be suitable for the customer''s financial situation, investment objectives, and risk tolerance.',
            '{}', NULL
        ),
        -- earnings_blackout
        (
            'standard', NULL,
            'restricted_asset', 'US-SEC', 'soft_warn',
            'Trading on material non-public information around earnings announcements may constitute insider trading under SEC Rule 10b-5.',
            '{}', NULL
        )
    ON CONFLICT DO NOTHING;
END;
$$;
