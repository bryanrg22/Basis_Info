"""
Authentication dependencies for FastAPI.

Provides Firebase token verification using FastAPI dependency injection.
"""

import logging
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
import firebase_admin
from firebase_admin import auth

logger = logging.getLogger(__name__)

# HTTP Bearer security scheme
security = HTTPBearer(auto_error=False)


class CurrentUser(BaseModel):
    """Authenticated user from Firebase token."""

    uid: str
    email: Optional[str] = None
    name: Optional[str] = None


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> CurrentUser:
    """
    Verify Firebase ID token and return current user.

    Raises HTTPException 401 if token is missing or invalid.
    """
    if credentials is None:
        logger.warning("Auth failed: No credentials provided")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    logger.debug(f"Auth: Received token starting with {token[:20]}...")

    # Ensure Firebase Admin SDK is initialized
    try:
        firebase_admin.get_app()
    except ValueError:
        # Not initialized, initialize it now
        logger.info("Initializing Firebase Admin SDK for auth")
        from ...firestore.client import _initialize_firebase
        _initialize_firebase()

    try:
        # Verify the Firebase ID token
        decoded_token = auth.verify_id_token(token)
        logger.info(f"Auth success: user {decoded_token.get('email', decoded_token['uid'])}")
        return CurrentUser(
            uid=decoded_token["uid"],
            email=decoded_token.get("email"),
            name=decoded_token.get("name"),
        )
    except firebase_admin.exceptions.FirebaseError as e:
        logger.error(f"Auth failed: Firebase error - {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as e:
        logger.error(f"Auth failed: {type(e).__name__} - {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[CurrentUser]:
    """
    Verify Firebase ID token if present, return None if no token.

    Useful for endpoints that work for both authenticated and anonymous users.
    """
    if credentials is None:
        return None

    token = credentials.credentials
    try:
        decoded_token = auth.verify_id_token(token)
        return CurrentUser(
            uid=decoded_token["uid"],
            email=decoded_token.get("email"),
            name=decoded_token.get("name"),
        )
    except Exception:
        return None
