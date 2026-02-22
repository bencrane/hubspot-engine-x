-- hubspot-engine-x initial schema
-- Multi-tenant HubSpot administration platform

-- ============================================================
-- Extensions
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- Enums
-- ============================================================

CREATE TYPE user_role AS ENUM ('org_admin', 'company_admin', 'company_member');
CREATE TYPE connection_status AS ENUM ('pending', 'connected', 'expired', 'revoked', 'error');
CREATE TYPE deployment_status AS ENUM ('pending', 'in_progress', 'succeeded', 'partial', 'failed', 'rolled_back');
CREATE TYPE deployment_type AS ENUM ('custom_object', 'custom_property', 'association', 'workflow', 'other');
CREATE TYPE conflict_severity AS ENUM ('green', 'yellow', 'red');
CREATE TYPE push_status AS ENUM ('queued', 'in_progress', 'succeeded', 'partial', 'failed');

-- ============================================================
-- Organizations
-- ============================================================

CREATE TABLE organizations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- Clients (org's customers)
-- ============================================================

CREATE TABLE clients (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    domain TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(org_id, domain)
);

CREATE INDEX idx_clients_org_id ON clients(org_id);

-- ============================================================
-- Users (people at the org)
-- ============================================================

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    client_id UUID REFERENCES clients(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    name TEXT,
    role user_role NOT NULL DEFAULT 'company_member',
    password_hash TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(org_id, email)
);

CREATE INDEX idx_users_org_id ON users(org_id);
CREATE INDEX idx_users_client_id ON users(client_id);
CREATE INDEX idx_users_email ON users(email);

-- ============================================================
-- API Tokens (machine-to-machine auth)
-- ============================================================

CREATE TABLE api_tokens (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    label TEXT,
    expires_at TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_api_tokens_org_id ON api_tokens(org_id);
CREATE INDEX idx_api_tokens_token_hash ON api_tokens(token_hash);

-- ============================================================
-- CRM Connections (OAuth metadata per client — no tokens stored)
-- ============================================================

CREATE TABLE crm_connections (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    nango_connection_id TEXT,
    status connection_status NOT NULL DEFAULT 'pending',
    hub_domain TEXT,
    hubspot_portal_id TEXT,
    scopes TEXT,
    last_used_at TIMESTAMPTZ,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(org_id, client_id)
);

CREATE INDEX idx_crm_connections_org_id ON crm_connections(org_id);
CREATE INDEX idx_crm_connections_client_id ON crm_connections(client_id);
CREATE INDEX idx_crm_connections_status ON crm_connections(status);

-- ============================================================
-- CRM Topology Snapshots (schema snapshots per client)
-- ============================================================

CREATE TABLE crm_topology_snapshots (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    connection_id UUID NOT NULL REFERENCES crm_connections(id) ON DELETE CASCADE,
    version INTEGER NOT NULL DEFAULT 1,
    snapshot JSONB NOT NULL,
    objects_count INTEGER,
    custom_objects_count INTEGER,
    pulled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(org_id, client_id, version)
);

CREATE INDEX idx_crm_topology_org_id ON crm_topology_snapshots(org_id);
CREATE INDEX idx_crm_topology_client_id ON crm_topology_snapshots(client_id);
CREATE INDEX idx_crm_topology_connection_id ON crm_topology_snapshots(connection_id);

-- ============================================================
-- CRM Conflict Reports (pre-deploy check results)
-- ============================================================

CREATE TABLE crm_conflict_reports (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    connection_id UUID NOT NULL REFERENCES crm_connections(id) ON DELETE CASCADE,
    topology_snapshot_id UUID REFERENCES crm_topology_snapshots(id),
    deployment_plan JSONB NOT NULL,
    findings JSONB NOT NULL,
    overall_severity conflict_severity NOT NULL,
    green_count INTEGER NOT NULL DEFAULT 0,
    yellow_count INTEGER NOT NULL DEFAULT 0,
    red_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_crm_conflicts_org_id ON crm_conflict_reports(org_id);
CREATE INDEX idx_crm_conflicts_client_id ON crm_conflict_reports(client_id);

-- ============================================================
-- CRM Deployments (log of what was deployed to client's HubSpot)
-- ============================================================

CREATE TABLE crm_deployments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    connection_id UUID NOT NULL REFERENCES crm_connections(id) ON DELETE CASCADE,
    conflict_report_id UUID REFERENCES crm_conflict_reports(id),
    deployed_by UUID NOT NULL REFERENCES users(id),
    deployment_type deployment_type NOT NULL,
    status deployment_status NOT NULL DEFAULT 'pending',
    plan JSONB NOT NULL,
    result JSONB,
    error_message TEXT,
    deployed_at TIMESTAMPTZ,
    rolled_back_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_crm_deployments_org_id ON crm_deployments(org_id);
CREATE INDEX idx_crm_deployments_client_id ON crm_deployments(client_id);
CREATE INDEX idx_crm_deployments_connection_id ON crm_deployments(connection_id);
CREATE INDEX idx_crm_deployments_status ON crm_deployments(status);

-- ============================================================
-- CRM Push Logs (record push history)
-- ============================================================

CREATE TABLE crm_push_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    connection_id UUID NOT NULL REFERENCES crm_connections(id) ON DELETE CASCADE,
    pushed_by UUID REFERENCES users(id),
    status push_status NOT NULL DEFAULT 'queued',
    object_type TEXT NOT NULL,
    records_total INTEGER NOT NULL DEFAULT 0,
    records_succeeded INTEGER NOT NULL DEFAULT 0,
    records_failed INTEGER NOT NULL DEFAULT 0,
    payload JSONB,
    result JSONB,
    error_message TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_crm_push_logs_org_id ON crm_push_logs(org_id);
CREATE INDEX idx_crm_push_logs_client_id ON crm_push_logs(client_id);
CREATE INDEX idx_crm_push_logs_connection_id ON crm_push_logs(connection_id);
CREATE INDEX idx_crm_push_logs_status ON crm_push_logs(status);

-- ============================================================
-- CRM Field Mappings (canonical-to-HubSpot property mapping)
-- ============================================================

CREATE TABLE crm_field_mappings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    canonical_object TEXT NOT NULL,
    canonical_field TEXT NOT NULL,
    hubspot_object TEXT NOT NULL,
    hubspot_property TEXT NOT NULL,
    transform_rule JSONB,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(org_id, client_id, canonical_object, canonical_field)
);

CREATE INDEX idx_crm_field_mappings_org_id ON crm_field_mappings(org_id);
CREATE INDEX idx_crm_field_mappings_client_id ON crm_field_mappings(client_id);
CREATE INDEX idx_crm_field_mappings_lookup ON crm_field_mappings(org_id, client_id, canonical_object);

-- ============================================================
-- Updated-at triggers
-- ============================================================

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_organizations_updated_at
    BEFORE UPDATE ON organizations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_clients_updated_at
    BEFORE UPDATE ON clients
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_crm_connections_updated_at
    BEFORE UPDATE ON crm_connections
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_crm_deployments_updated_at
    BEFORE UPDATE ON crm_deployments
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_crm_push_logs_updated_at
    BEFORE UPDATE ON crm_push_logs
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_crm_field_mappings_updated_at
    BEFORE UPDATE ON crm_field_mappings
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ============================================================
-- Tenant integrity triggers
-- ============================================================

CREATE OR REPLACE FUNCTION check_client_org_integrity()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.client_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM clients WHERE id = NEW.client_id AND org_id = NEW.org_id
    ) THEN
        RAISE EXCEPTION 'client_id does not belong to org_id';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Ensure client belongs to same org on all child tables
CREATE TRIGGER trg_users_org_integrity
    BEFORE INSERT OR UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION check_client_org_integrity();

CREATE TRIGGER trg_crm_connections_org_integrity
    BEFORE INSERT OR UPDATE ON crm_connections
    FOR EACH ROW EXECUTE FUNCTION check_client_org_integrity();

CREATE TRIGGER trg_crm_topology_org_integrity
    BEFORE INSERT OR UPDATE ON crm_topology_snapshots
    FOR EACH ROW EXECUTE FUNCTION check_client_org_integrity();

CREATE TRIGGER trg_crm_conflict_reports_org_integrity
    BEFORE INSERT OR UPDATE ON crm_conflict_reports
    FOR EACH ROW EXECUTE FUNCTION check_client_org_integrity();

CREATE TRIGGER trg_crm_deployments_org_integrity
    BEFORE INSERT OR UPDATE ON crm_deployments
    FOR EACH ROW EXECUTE FUNCTION check_client_org_integrity();

CREATE TRIGGER trg_crm_push_logs_org_integrity
    BEFORE INSERT OR UPDATE ON crm_push_logs
    FOR EACH ROW EXECUTE FUNCTION check_client_org_integrity();

CREATE TRIGGER trg_crm_field_mappings_org_integrity
    BEFORE INSERT OR UPDATE ON crm_field_mappings
    FOR EACH ROW EXECUTE FUNCTION check_client_org_integrity();

-- Ensure conflict_report belongs to same org on deployments
CREATE OR REPLACE FUNCTION check_conflict_report_org_integrity()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.conflict_report_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM crm_conflict_reports WHERE id = NEW.conflict_report_id AND org_id = NEW.org_id
    ) THEN
        RAISE EXCEPTION 'conflict_report_id does not belong to org_id';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_crm_deployments_conflict_report_integrity
    BEFORE INSERT OR UPDATE ON crm_deployments
    FOR EACH ROW EXECUTE FUNCTION check_conflict_report_org_integrity();

-- ============================================================
-- RLS enabled (policies to be added later)
-- ============================================================

ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE clients ENABLE ROW LEVEL SECURITY;
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_connections ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_topology_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_deployments ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_conflict_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_push_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_field_mappings ENABLE ROW LEVEL SECURITY;