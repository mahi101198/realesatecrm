"""External Provider Integrations Package.

Per skills/system.md rule #31: provider integration code (HTTP requests,
authentication, timeouts, retries, response parsing, provider error mapping)
is isolated here, one subpackage per provider. Business services must never
contain raw provider HTTP/SDK code -- they call a clean client interface.
"""
