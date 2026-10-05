class DomainError(Exception):
    def __init__(self, message: str, code: str = "invalid_request", status: int = 422, details=None):
        self.message, self.code, self.status, self.details = message, code, status, details


def require(value, message="资源不存在"):
    if value is None:
        raise DomainError(message, "not_found", 404)
    return value
