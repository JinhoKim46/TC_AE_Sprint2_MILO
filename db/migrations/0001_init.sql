-- MILO schema, migration 0001 (SPEC section 15 as rewritten by D72, D89, D90, D97, D98, D129, D130, D142).
--
-- Target: a Supabase Postgres project (auth.users exists). Applied by `make migrate`, never by tests.
-- Ids: profiles.id, sessions.id and turns.id are uuid; every other table uses bigint generated always as identity.
-- Row-level security: every owner table reads current_setting('milo.user_id', true)::uuid and nothing else (D129).
-- The app connects as milo_app and runs `SET LOCAL milo.user_id = '<uuid>'` per transaction (D74). Operators use
-- milo_operator: SELECT everywhere, DELETE on customers (cascading to the owner's rows), INSERT on audit_log (D130).
-- The owner sets the passwords of milo_app and milo_operator by hand in the dashboard; no secret is stored here.

create extension if not exists pgcrypto;

create table if not exists schema_migrations (
    version    text primary key,
    checksum   text not null,
    applied_at timestamptz not null default now()
);

-- ---- roles (D74, D130) ----
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'milo_app') then
        create role milo_app login nosuperuser nocreatedb nocreaterole noinherit nobypassrls;
    end if;
    if not exists (select 1 from pg_roles where rolname = 'milo_operator') then
        create role milo_operator login nosuperuser nocreatedb nocreaterole noinherit nobypassrls;
    end if;
end
$$;

-- The administrative role may SET ROLE to both (D153), so the RLS test needs no role passwords. On PostgreSQL 16+ a
-- CREATEROLE role that creates a role holds only ADMIN membership, so the grant names SET explicitly; INHERIT lets the administrative role drop the roles' objects at db-reset. Granting
-- again is harmless, which keeps the file idempotent.
grant milo_app to current_user with set true, inherit true;
grant milo_operator to current_user with set true, inherit true;

grant usage on schema public to milo_app, milo_operator;

-- ---- tables ----

create table profiles (
    id           uuid primary key references auth.users (id) on delete cascade,
    email        text,
    display_name text,
    role         text not null default 'owner' check (role in ('owner', 'operator')),
    created_at   timestamptz not null default now(),
    last_seen_at timestamptz
);

create table customers (
    user_id        uuid primary key references profiles (id) on delete cascade,
    company_name   text,
    industry       text,
    employee_count integer,
    locations      integer,
    owner_name     text,
    owner_role     text,
    owner_email    text,
    created_at     timestamptz not null default now()
);

create table consents (
    id      bigint generated always as identity primary key,
    user_id uuid not null references customers (user_id) on delete cascade,
    purpose text not null check (purpose in ('interview', 'aggregate')),
    version text not null,
    at      timestamptz not null default now()
);
create index consents_user_idx on consents (user_id, purpose);

create table sessions (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null references customers (user_id) on delete cascade,
    title       text,
    status      text not null default 'active' check (status in ('active', 'submitted', 'archived')),
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now(),
    archived_at timestamptz
);
create index sessions_user_idx on sessions (user_id, updated_at desc);

create table messages (
    id         bigint generated always as identity primary key,
    session_id uuid not null references sessions (id) on delete cascade,
    role       text not null check (role in ('owner', 'assistant', 'system')),
    content    text not null,
    created_at timestamptz not null default now(),
    trace_hash text
);
create index messages_session_idx on messages (session_id, id);

create table turns (
    id          uuid primary key default gen_random_uuid(),
    session_id  uuid not null references sessions (id) on delete cascade,
    status      text not null default 'running' check (status in ('running', 'done', 'error')),
    created_at  timestamptz not null default now(),
    finished_at timestamptz
);
create index turns_session_idx on turns (session_id, created_at);

create table turn_events (
    id           bigint generated always as identity primary key,
    turn_id      uuid not null references turns (id) on delete cascade,
    seq          integer not null,
    type         text not null,
    payload_json jsonb not null default '{}'::jsonb,
    created_at   timestamptz not null default now(),
    unique (turn_id, seq)
);

create table facts (
    session_id        uuid not null references sessions (id) on delete cascade,
    field_id          text not null,
    value_json        jsonb,
    unit              text,
    period            text,
    status            text not null check (status in ('reported', 'confirmed', 'estimated', 'unknown', 'conflicting')),
    source_message_id bigint references messages (id) on delete set null,
    updated_at        timestamptz not null default now(),
    primary key (session_id, field_id)
);

create table fact_history (
    id                bigint generated always as identity primary key,
    session_id        uuid not null references sessions (id) on delete cascade,
    field_id          text not null,
    value_json        jsonb,
    status            text not null,
    source_message_id bigint,
    changed_at        timestamptz not null default now()
);
create index fact_history_idx on fact_history (session_id, field_id, id);

create table evidence (
    id         bigint generated always as identity primary key,
    session_id uuid not null references sessions (id) on delete cascade,
    message_id bigint references messages (id) on delete set null,
    kind       text not null check (kind in ('customer_statement', 'uploaded_document', 'kb_chunk', 'calculation')),
    ref        text,
    score      double precision,
    text_hash  text,
    created_at timestamptz not null default now()
);
create index evidence_session_idx on evidence (session_id);

create table submissions (
    id                 bigint generated always as identity primary key,
    session_id         uuid not null references sessions (id) on delete cascade,
    revision           integer not null,
    submitted_at       timestamptz not null default now(),
    status             text not null default 'received' check (status in ('received', 'report_ready', 'failed', 'withdrawn')),
    spec_snapshot_json jsonb not null,
    report_json        jsonb,
    report_html        text,
    estimate_status    text,
    cost_low           numeric,
    cost_base          numeric,
    cost_high          numeric,
    currency           text,
    rate_card_version  text,
    model_id           text,
    unique (session_id, revision)
);

-- A submission revision is immutable (D41): the report fields may be filled once and the status may change;
-- the snapshot and identity of the revision never change.
create or replace function submissions_immutable() returns trigger language plpgsql as $$
begin
    if new.session_id <> old.session_id or new.revision <> old.revision
       or new.spec_snapshot_json <> old.spec_snapshot_json or new.submitted_at <> old.submitted_at then
        raise exception 'a submission revision is immutable';
    end if;
    return new;
end
$$;
drop trigger if exists submissions_immutable_trg on submissions;
create trigger submissions_immutable_trg before update on submissions
    for each row execute function submissions_immutable();

create table approvals (
    id         bigint generated always as identity primary key,
    session_id uuid not null references sessions (id) on delete cascade,
    turn_id    uuid references turns (id) on delete set null,
    tool_name  text not null,
    args_hash  text not null,
    status     text not null default 'pending' check (status in ('pending', 'approved', 'kept', 'used', 'expired')),
    created_at timestamptz not null default now(),
    expires_at timestamptz not null
);
create index approvals_session_idx on approvals (session_id, status);

create table audit_log (
    id            bigint generated always as identity primary key,
    actor_user_id uuid,
    action        text not null,
    target        text,
    detail_json   jsonb not null default '{}'::jsonb,
    at            timestamptz not null default now()
);

create table traces (
    id          bigint generated always as identity primary key,
    turn_id     uuid references turns (id) on delete cascade,
    ts          timestamptz not null default now(),
    node        text not null,
    duration_ms double precision,
    status      text,
    error_code  text,
    detail_json jsonb not null default '{}'::jsonb
);
create index traces_turn_idx on traces (turn_id, id);

create table field_requests (
    id         bigint generated always as identity primary key,
    user_id    uuid not null references customers (user_id) on delete cascade,
    session_id uuid references sessions (id) on delete cascade,
    field_id   text not null,
    note       text,
    status     text not null default 'open' check (status in ('open', 'done', 'declined')),
    created_at timestamptz not null default now()
);

create table deletion_requests (
    id          bigint generated always as identity primary key,
    user_id     uuid not null references customers (user_id) on delete cascade,
    reason      text,
    status      text not null default 'open' check (status in ('open', 'done', 'declined')),
    created_at  timestamptz not null default now(),
    resolved_at timestamptz
);

-- Counted here so limits hold across workers and restarts (D142). Keyed by scope and key (account, IP, email),
-- not by owner, so its policy is the app role's access rather than an owner filter.
create table rate_limits (
    scope        text not null,
    key          text not null,
    window_start timestamptz not null,
    count        integer not null default 0,
    primary key (scope, key, window_start)
);

-- ---- profile trigger (D89) ----
create or replace function public.handle_confirmed_user() returns trigger
    language plpgsql security definer as $$
begin
    if new.email_confirmed_at is not null then
        insert into public.profiles (id, email) values (new.id, new.email) on conflict (id) do nothing;
    end if;
    return new;
end
$$;
drop trigger if exists on_auth_user_confirmed on auth.users;
create trigger on_auth_user_confirmed after insert or update of email_confirmed_at on auth.users
    for each row execute function public.handle_confirmed_user();

-- ---- ownership helpers (security invoker: under FORCE the table owner is subject to policies too, so the helpers
-- run as the caller, whose own policies on sessions and turns give the same answer; no policy calls itself) ----
create or replace function public.milo_user() returns uuid language sql stable as $$
    select nullif(current_setting('milo.user_id', true), '')::uuid
$$;

create or replace function public.owns_session(sid uuid) returns boolean language sql stable security invoker as $$
    select exists (select 1 from public.sessions s where s.id = sid and s.user_id = public.milo_user())
$$;

create or replace function public.owns_turn(tid uuid) returns boolean language sql stable security invoker as $$
    select exists (
        select 1 from public.turns t join public.sessions s on s.id = t.session_id
        where t.id = tid and s.user_id = public.milo_user()
    )
$$;

-- ---- row-level security (D74, D91, D129) ----
-- Owner policies: `to milo_app`, predicate reads the per-transaction setting. Operator policies: read everywhere.

alter table profiles enable row level security;
create policy profiles_owner on profiles for all to milo_app
    using (id = nullif(current_setting('milo.user_id', true), '')::uuid)
    with check (id = nullif(current_setting('milo.user_id', true), '')::uuid);

alter table customers enable row level security;
create policy customers_owner on customers for all to milo_app
    using (user_id = nullif(current_setting('milo.user_id', true), '')::uuid)
    with check (user_id = nullif(current_setting('milo.user_id', true), '')::uuid);

alter table consents enable row level security;
create policy consents_owner on consents for all to milo_app
    using (user_id = nullif(current_setting('milo.user_id', true), '')::uuid)
    with check (user_id = nullif(current_setting('milo.user_id', true), '')::uuid);

alter table sessions enable row level security;
create policy sessions_owner on sessions for all to milo_app
    using (user_id = nullif(current_setting('milo.user_id', true), '')::uuid)
    with check (user_id = nullif(current_setting('milo.user_id', true), '')::uuid);

alter table field_requests enable row level security;
create policy field_requests_owner on field_requests for all to milo_app
    using (user_id = nullif(current_setting('milo.user_id', true), '')::uuid)
    with check (user_id = nullif(current_setting('milo.user_id', true), '')::uuid);

alter table deletion_requests enable row level security;
create policy deletion_requests_owner on deletion_requests for all to milo_app
    using (user_id = nullif(current_setting('milo.user_id', true), '')::uuid)
    with check (user_id = nullif(current_setting('milo.user_id', true), '')::uuid);

alter table messages enable row level security;
create policy messages_owner on messages for all to milo_app
    using (owns_session(session_id)) with check (owns_session(session_id));

alter table turns enable row level security;
create policy turns_owner on turns for all to milo_app
    using (owns_session(session_id)) with check (owns_session(session_id));

alter table turn_events enable row level security;
create policy turn_events_owner on turn_events for all to milo_app
    using (owns_turn(turn_id)) with check (owns_turn(turn_id));

alter table facts enable row level security;
create policy facts_owner on facts for all to milo_app
    using (owns_session(session_id)) with check (owns_session(session_id));

alter table fact_history enable row level security;
create policy fact_history_owner on fact_history for all to milo_app
    using (owns_session(session_id)) with check (owns_session(session_id));

alter table evidence enable row level security;
create policy evidence_owner on evidence for all to milo_app
    using (owns_session(session_id)) with check (owns_session(session_id));

alter table submissions enable row level security;
create policy submissions_owner on submissions for all to milo_app
    using (owns_session(session_id)) with check (owns_session(session_id));

alter table approvals enable row level security;
create policy approvals_owner on approvals for all to milo_app
    using (owns_session(session_id)) with check (owns_session(session_id));

alter table traces enable row level security;
create policy traces_owner on traces for all to milo_app
    using (turn_id is not null and owns_turn(turn_id)) with check (turn_id is not null and owns_turn(turn_id));

-- audit_log: owners never read it. Only the operator policies below apply.
alter table audit_log enable row level security;

-- rate_limits: pre-login counters have no owner id; the app role may count, owners' rows are not exposed by it.
alter table rate_limits enable row level security;
create policy rate_limits_app on rate_limits for all to milo_app using (true) with check (true);

alter table schema_migrations enable row level security;

-- ---- force row level security (SR A-09c) ----
-- The table owner is subject to the policies too. schema_migrations is excluded: migrate.py writes it as the owner.
-- The profile trigger runs as the migrating role, so it gets its own insert policy.
create policy profiles_trigger_insert on profiles for insert to current_user with check (true);
alter table profiles force row level security;
alter table customers force row level security;
alter table consents force row level security;
alter table sessions force row level security;
alter table messages force row level security;
alter table turns force row level security;
alter table turn_events force row level security;
alter table facts force row level security;
alter table fact_history force row level security;
alter table evidence force row level security;
alter table submissions force row level security;
alter table approvals force row level security;
alter table audit_log force row level security;
alter table traces force row level security;
alter table field_requests force row level security;
alter table deletion_requests force row level security;
alter table rate_limits force row level security;

-- operator: SELECT on every table, nothing else except the two writes of D130
create policy profiles_operator_read on profiles for select to milo_operator using (true);
create policy customers_operator_read on customers for select to milo_operator using (true);
create policy consents_operator_read on consents for select to milo_operator using (true);
create policy sessions_operator_read on sessions for select to milo_operator using (true);
create policy messages_operator_read on messages for select to milo_operator using (true);
create policy turns_operator_read on turns for select to milo_operator using (true);
create policy turn_events_operator_read on turn_events for select to milo_operator using (true);
create policy facts_operator_read on facts for select to milo_operator using (true);
create policy fact_history_operator_read on fact_history for select to milo_operator using (true);
create policy evidence_operator_read on evidence for select to milo_operator using (true);
create policy submissions_operator_read on submissions for select to milo_operator using (true);
create policy approvals_operator_read on approvals for select to milo_operator using (true);
create policy audit_log_operator_read on audit_log for select to milo_operator using (true);
create policy traces_operator_read on traces for select to milo_operator using (true);
create policy field_requests_operator_read on field_requests for select to milo_operator using (true);
create policy deletion_requests_operator_read on deletion_requests for select to milo_operator using (true);
create policy rate_limits_operator_read on rate_limits for select to milo_operator using (true);
create policy schema_migrations_operator_read on schema_migrations for select to milo_operator using (true);
create policy customers_operator_delete on customers for delete to milo_operator using (true);
create policy audit_log_operator_insert on audit_log for insert to milo_operator with check (true);

-- ---- grants (D130 for the operator; least privilege for the app) ----
revoke all on all tables in schema public from milo_app, milo_operator;

grant select on all tables in schema public to milo_operator;
grant delete on customers to milo_operator;
grant insert on audit_log to milo_operator;

grant select on profiles to milo_app;
grant update (last_seen_at) on profiles to milo_app;
grant select, insert, update on customers to milo_app;
grant select, insert on consents to milo_app;
grant select, insert, update on sessions to milo_app;
grant select, insert on messages to milo_app;
grant select, insert, update on turns to milo_app;
grant select, insert on turn_events to milo_app;
grant select, insert, update on facts to milo_app;
grant select, insert on fact_history to milo_app;
grant select, insert on evidence to milo_app;
grant select, insert, update on submissions to milo_app;
grant select, insert, update on approvals to milo_app;
grant select, insert on traces to milo_app;
grant select, insert on field_requests to milo_app;
grant select, insert on deletion_requests to milo_app;
grant select, insert, update, delete on rate_limits to milo_app;

grant usage on all sequences in schema public to milo_app, milo_operator;
grant execute on function milo_user(), owns_session(uuid), owns_turn(uuid) to milo_app, milo_operator;
