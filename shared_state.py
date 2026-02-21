"""Thin helpers to standardize shared_data lock-based access."""


def snapshot_locked(shared_data, reader):
    """Read a shared-data snapshot under lock using a caller-provided reader."""
    with shared_data["lock"]:
        return reader(shared_data)


def mutate_locked(shared_data, mutator):
    """Apply a mutation under lock using a caller-provided mutator."""
    with shared_data["lock"]:
        return mutator(shared_data)


def update_locked(shared_data, **updates):
    """Apply a shallow update under lock."""
    with shared_data["lock"]:
        shared_data.update(updates)
