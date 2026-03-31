import urllib.parse

import jwt
from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.tokens import TokenError

User = get_user_model()


@database_sync_to_async
def get_user_from_token(token_str):
    try:
        auth = JWTAuthentication()
        validated_token = auth.get_validated_token(token_str)
        user_id = validated_token['user_id']
        user = User.objects.get(id=user_id)
        print(f"[JWT] Token validated for user {user.username} (ID: {user.id})")
        return user
    except (jwt.InvalidTokenError, TokenError, User.DoesNotExist) as exc:
        print(f"[JWT] Token validation failed: {type(exc).__name__}: {exc}")
        return None
    except AttributeError as exc:
        print(f"[JWT] Token validation error: {exc}")
        return None


class JWTAuthMiddleware:
    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'websocket':
            query_string = scope.get('query_string', b'').decode('utf-8')
            token = None

            if query_string:
                try:
                    params = urllib.parse.parse_qs(query_string)
                    token = params.get('token', [None])[0]
                except Exception as exc:
                    print(f"[JWT] Error parsing query string: {exc}")

            if token:
                user = await get_user_from_token(token)
                scope['user'] = user or AnonymousUser()
            else:
                scope['user'] = AnonymousUser()

        return await self.inner(scope, receive, send)
