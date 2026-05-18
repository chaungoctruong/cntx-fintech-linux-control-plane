"""Bot-token licensing helpers.

This package keeps bot-token concerns out of the control-plane core:
- catalog: validates that licensed bots exist as packages in bot-trading/
- crypto: hashes raw token material with backend secret key material
- expiry_reconciler: expires entitlements and stops running deployments
"""
