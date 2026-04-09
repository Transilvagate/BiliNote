from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable


class SerialTaskExecutor:
    """使用单线程队列串行执行任务，避免多个笔记任务并发互相污染状态。"""

    def __init__(self):
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="note-serial")

    def run(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        future: Future = self._pool.submit(fn, *args, **kwargs)
        return future.result()

    def shutdown(self, wait: bool = True):
        self._pool.shutdown(wait=wait)


task_serial_executor = SerialTaskExecutor()
