import os

# tests never see real credentials
os.environ.update(
    PAYPAL_CLIENT_ID="test-id",
    PAYPAL_CLIENT_SECRET="test-secret",
    PAYPAL_WEBHOOK_ID="WH-TEST",
    PAYPAL_BASE_URL="https://api-m.sandbox.paypal.com",
    DATABASE_URL="postgresql://unused",
)
