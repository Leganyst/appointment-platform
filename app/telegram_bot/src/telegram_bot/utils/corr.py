import uuid


def new_corr_id() -> str:
    return uuid.uuid4().hex
