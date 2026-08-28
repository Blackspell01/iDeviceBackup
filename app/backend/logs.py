import logging

from backend.backup import backup


class UiLog(logging.Handler):
    def __init__(self):
        super().__init__()
        self.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record):
        backup.log(self.format(record))


def setup():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    logging.getLogger("pymobiledevice3").setLevel(logging.DEBUG)
    logging.getLogger().addHandler(UiLog())
