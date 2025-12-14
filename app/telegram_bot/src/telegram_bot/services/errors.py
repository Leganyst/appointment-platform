import grpc


def user_friendly_error(exc: grpc.aio.AioRpcError) -> str:
    code = exc.code()
    if code == grpc.StatusCode.INVALID_ARGUMENT:
        return "Проверьте введённые данные."
    if code == grpc.StatusCode.NOT_FOUND:
        return "Не найдено или устарело."
    if code == grpc.StatusCode.FAILED_PRECONDITION:
        return "Слот недоступен или занят."
    if code in {grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED}:
        return "Сервис временно недоступен, попробуйте позже."
    return "Ошибка сервиса, попробуйте позже."
