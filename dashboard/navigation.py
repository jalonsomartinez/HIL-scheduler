"""Route helpers for dashboard navigation."""

PRIVATE_ROUTES = {
    "/status",
    "/plots",
    "/grid-map",
    "/manual-schedule",
    "/api-schedule",
    "/logs",
}

PUBLIC_ROUTES = {
    "/status",
    "/plots",
    "/grid-map",
}

PRIVATE_DEFAULT_ROUTE = "/status"
PUBLIC_DEFAULT_ROUTE = "/status"


def _normalize_pathname(pathname):
    text = str(pathname or "").strip()
    if not text:
        return "/"
    path = text.split("?", 1)[0].split("#", 1)[0].strip()
    if not path:
        return "/"
    if not path.startswith("/"):
        path = f"/{path}"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return path or "/"


def normalize_private_route(pathname):
    path = _normalize_pathname(pathname)
    if path in PRIVATE_ROUTES:
        return path
    return PRIVATE_DEFAULT_ROUTE


def normalize_public_route(pathname):
    path = _normalize_pathname(pathname)
    if path in PUBLIC_ROUTES:
        return path
    return PUBLIC_DEFAULT_ROUTE


def page_section_class(is_active):
    if bool(is_active):
        return "page-section page-section--active"
    return "page-section"


def resolve_menu_open_state(*, trigger_id, previous_open, toggle_trigger_id):
    if str(trigger_id or "") == str(toggle_trigger_id):
        return not bool(previous_open)
    return False
