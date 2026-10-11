"""Restricted BORIS outreach service agent: opt-in router.

Not mounted automatically. Requires a separately provisioned SHA-256 token digest
and owner user ID in the backend service environment; no browser session reuse.
This router intentionally offers *readiness only* until security review.
"""
import hashlib
import hmac
import os
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/service/outreach", tags=["outreach-agent"])

class AgentPrincipal(BaseModel):
    owner_id: int
    scope: str = "outreach:read"

def authenticate_agent(authorization: str | None = Header(default=None)) -> AgentPrincipal:
    digest = os.environ.get("BORIS_OUTREACH_AGENT_TOKEN_SHA256", "").strip().lower()
    owner = os.environ.get("BORIS_OUTREACH_AGENT_OWNER_ID", "").strip()
    if len(digest) != 64 or not owner.isdecimal():
        raise HTTPException(status_code=503, detail="agent_not_provisioned")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="agent_authorization_required")
    token = authorization[7:]
    if len(token) < 40 or len(token) > 256:
        raise HTTPException(status_code=401, detail="agent_unauthorized")
    incoming = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(incoming, digest):
        raise HTTPException(status_code=401, detail="agent_unauthorized")
    return AgentPrincipal(owner_id=int(owner))

@router.get("/health")
def health(agent: AgentPrincipal = Depends(authenticate_agent)):
    return {"ok": True, "principal": "boris-outreach-agent", "scope": agent.scope,
            "sending_enabled": False, "owner_id": agent.owner_id}
