"""Pull-based ingestion of Omi conversations into internal tools.

Reads conversations from the Omi Developer API with a read-only key, normalizes
them into a stable internal shape, and delivers each new one to configured sinks.

Nothing in this package opens an inbound port: every call is outbound to
``api.omi.me``. See README.md for why that matters.
"""

__version__ = "0.1.0"
