import keyring

SERVICE = "aio-cctv"


def set_password(cam_id: str, password: str) -> None:
    keyring.set_password(SERVICE, cam_id, password)


def get_password(cam_id: str) -> str:
    return keyring.get_password(SERVICE, cam_id) or ""
