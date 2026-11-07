"""User queue range. Older native hosts still negotiate at most three slots."""
MAX_IN_FLIGHT = 16
LEGACY_MAX_IN_FLIGHT = 3


def clamp_in_flight(value, default=2):
    try:
        return max(1, min(MAX_IN_FLIGHT, int(value)))
    except (TypeError, ValueError, OverflowError):
        return default


def configured_in_flight(settings=None, *, capacity=None):
    """Queue allocation after backend and synchronous-mode limits.

    Before loading a backend, legacy has a known three-slot ceiling. Native
    callers pass the capacity supported by their loaded library explicitly.
    """
    settings = settings or {}
    if (settings.get('host_tiled_mode') or settings.get('host_submission', 'merged') != 'merged'
            or not settings.get('host_persistent_buffers', True)):
        return 1
    if capacity is None:
        capacity = LEGACY_MAX_IN_FLIGHT if settings.get('host_backend') == 'legacy' else MAX_IN_FLIGHT
    return min(clamp_in_flight(settings.get('host_in_flight', 2)), max(1, int(capacity)))
