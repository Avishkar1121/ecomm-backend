
import jwt
from app.core.config import settings

# Paste your JWT here
token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJlbWFpbCI6ImF2aUBleGFtcGxlLmNvbSIsIm5hbWUiOiJhdmkiLCJyb2xlcyI6WyJidXllciJdLCJzdWIiOiJjNzA2ZTcyMi03NDQyLTQ4NDUtYWU1Ny00YTQzYTI0MGNjYjIiLCJ0eXBlIjoiYWNjZXNzIiwiaXNzIjoibWFya2V0aHViIiwiaWF0IjoxNzkyMDc5MDU0LCJleHAiOjE3OTIwNzk5NTR9.Xr2cC3rvOe054Lau7a3vmA0Pu2o8pz1BEbvJVqaZYf4"

# IMPORTANT:
# Replace settings.secret_key with the exact secret setting
# used inside app/core/security.py by create_access_token().
secret = settings.jwt_secret

try:
    payload = jwt.decode(
        token,
        secret,
        algorithms=["HS256"],
        issuer="markethub",
    )

    print("Signature is VALID")
    print("Token claims:")
    print(payload)

except jwt.ExpiredSignatureError:
    print("Signature may be valid, but the token has expired.")

except jwt.InvalidSignatureError:
    print("Invalid signature: check your signing secret.")

except jwt.InvalidTokenError as e:
    print("Token validation failed:", e)
