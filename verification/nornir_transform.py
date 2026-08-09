from nornir.core.inventory import ConnectionOptions

from secrets_resolver import resolve


def inject_credentials(host):
    """Nornir transform_function — resolves ${VAR} placeholders on each host
    after inventory load, before any task runs.

    username/password are transparently merged host->group->defaults on every
    read (Host.__getattribute__ handles that), so resolving them here and
    writing back is enough.

    The netmiko `secret` (enable password) lives under connection_options,
    which is NOT covered by that merge-on-read behavior — it's only merged at
    actual-connection time, and that merge replaces `extras` as a whole dict
    rather than per-key. So pull the already-merged view first, resolve just
    `secret` in place, then write it back at host level — this makes it win
    over the group's still-templated value without dropping `device_type`.
    """
    host.username = resolve(host.username)
    host.password = resolve(host.password)

    merged = host._get_connection_options_recursively("netmiko")
    extras = dict(merged.extras) if merged and merged.extras else {}
    if "secret" in extras:
        extras["secret"] = resolve(extras["secret"])
    host.connection_options["netmiko"] = ConnectionOptions(extras=extras)
