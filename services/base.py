"""Business exception boundary and explicit service liveness."""
import copy
import threading
from framework import api


def ok(data=None):
    return api.YomkResponse(api.ResStatus.eOk, "ok", copy.deepcopy(data))


def require(condition, message):
    if not condition:
        raise ValueError(message)


class ServiceRequestError(Exception):
    def __init__(self, response):
        super().__init__(response.msg)
        self.status = response.status


def request_data(url, message=None):
    response = api.request(url, message)
    if response.status != api.ResStatus.eOk:
        raise ServiceRequestError(response)
    return response.data


class MomoService(api.YomkService):
    def __init__(self, server):
        super().__init__(server)
        self.lock = threading.RLock()
        self.alive = False

    def install(self, endpoint, handler, message, description):
        def dispatch(pkg):
            with self.lock:
                if not self.alive:
                    return api.YomkResponse(api.ResStatus.eNo, "service is stopped")
                try:
                    return handler(pkg)
                except ServiceRequestError as exc:
                    return api.YomkResponse(exc.status, str(exc))
                except (ValueError, TypeError, KeyError, IndexError) as exc:
                    return api.YomkResponse(api.ResStatus.eInvalid, str(exc))
                except OSError as exc:
                    return api.YomkResponse(api.ResStatus.eNo, "本地保存失败：" + str(exc))
        self.install_func(endpoint, dispatch, message, description)

    def deinit(self):
        with self.lock:
            self.alive = False
