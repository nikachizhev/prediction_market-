from market.bot_jobs import process_one


def drain_jobs():
    """Exercise queued delivery inside a test's DB transaction (no connection cleanup)."""
    while process_one():
        pass
