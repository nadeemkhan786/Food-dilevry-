class ApiError(Exception):
    
    def __init__(self, status, message=None):
        self.status = status
        self.message = message

    def __str__(self):
        if self.message is None:
            return self.status
        else:
            return "%s (%s)" % (self.status, self.message)

class TransportError(Exception):
    

    def __init__(self, base_exception=None):
        self.base_exception = base_exception

    def __str__(self):
        if self.base_exception:
            return str(self.base_exception)

        return "An unknown error occurred."

class HTTPError(TransportError):
    
    def __init__(self, status_code):
        self.status_code = status_code

    def __str__(self):
        return "HTTP Error: %d" % self.status_code

class Timeout(Exception):
    
    pass

class _RetriableRequest(Exception):

    pass

class _OverQueryLimit(ApiError, _RetriableRequest):
    
    pass
