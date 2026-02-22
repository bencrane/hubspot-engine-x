from dataclasses import dataclass


@dataclass
class AuthContext:
    org_id: str
    user_id: str
    role: str
    permissions: list[str]
    client_id: str | None
    auth_method: str  # "api_token", "session", or "super_admin"


ROLE_PERMISSIONS: dict[str, list[str]] = {
    "org_admin": [
        "connections.read",
        "connections.write",
        "topology.read",
        "deploy.write",
        "push.write",
        "workflows.read",
        "workflows.write",
        "org.manage",
    ],
    "company_admin": [
        "connections.read",
        "topology.read",
        "workflows.read",
    ],
    "company_member": [
        "connections.read",
        "topology.read",
    ],
}
