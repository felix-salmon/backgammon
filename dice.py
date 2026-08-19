import secrets


def roll_dice():
    """Roll two dice using a cryptographically decent RNG (fine for a friendly game)."""
    return (secrets.randbelow(6) + 1, secrets.randbelow(6) + 1)
