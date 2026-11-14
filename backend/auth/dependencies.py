from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError

from backend.auth.jwt_handler import verify_access_token

security = HTTPBearer()


def get_current_claims(
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    try:
        token = credentials.credentials

        payload = verify_access_token(token)

        email = payload.get("sub")

        if not isinstance(email, str) or not email:
            raise HTTPException(
                status_code=401,
                detail="Invalid token"
            )

        return payload

    except JWTError:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token"
        )


def get_current_user(claims: dict = Depends(get_current_claims)):
    return claims["sub"]


def document_owner(claims):
    owner = claims.get("user_id")
    if not isinstance(owner, str) or not owner:
        raise HTTPException(status_code=401, detail="Sign in again to use documents.")
    return owner


def get_document_owner(claims: dict = Depends(get_current_claims)):
    return document_owner(claims)
