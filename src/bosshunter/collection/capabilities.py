"""Server-side capability boundaries for platform-specific workflows."""

PLATFORM_CAPABILITIES: dict[str, frozenset[str]] = {
    "boss": frozenset({"collect", "score", "greet", "deliver", "monitor"}),
    # Zhilian uses its own action adapter.  The shared workflow may schedule
    # these capabilities, but each adapter must still verify the page state
    # before recording a successful action.
    "zhilian": frozenset({"collect", "score", "greet", "deliver", "monitor"}),
}


def platform_supports(platform: str, capability: str) -> bool:
    return capability in PLATFORM_CAPABILITIES.get(str(platform), frozenset())
