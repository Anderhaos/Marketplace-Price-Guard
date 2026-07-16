from datetime import datetime
from pathlib import Path


class ActionLogger:
    def __init__(self, log_path):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, message):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with self.log_path.open("a", encoding="utf-8") as file:
            file.write(f"{now} - {message}\n")

