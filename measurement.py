"""Write numbered CSV recordings with native sensor timestamps."""

import csv
from pathlib import Path
import re

from stream import SENSORS


class Measurement:
    def __init__(self, folder, base, sensors):
        base = base.strip()
        if not re.fullmatch(r"[\w-]+", base):
            raise ValueError("Use letters, numbers, underscores or hyphens in the base filename.")
        if not sensors:
            raise ValueError("Select at least one sensor.")
        folder = Path(folder).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        pattern = re.compile(rf"{re.escape(base)}_(\d+)\.csv", re.IGNORECASE)
        existing = (pattern.fullmatch(path.name) for path in folder.iterdir())
        number = 1 + max((int(match[1]) for match in existing if match), default=0)
        while True:
            self.path = folder / f"{base}_{number:03d}.csv"
            try:
                self.file = self.path.open("x", encoding="utf-8", newline="")
                break
            except FileExistsError:
                number += 1
        self.sensors = tuple(sensors)
        self.counts = dict.fromkeys(self.sensors, 0)
        try:
            self.writer = csv.writer(self.file)
            self.writer.writerow(("sensor", "time_s", "x", "y", "z", "unit"))
            self.file.flush()
        except OSError:
            self.file.close()
            raise

    def write(self, samples):
        wrote = False
        for key in self.sensors:
            rows = samples.get(key)
            if rows is None or not len(rows):
                continue
            unit = SENSORS[key][1]
            for row in rows:
                axes = list(row[1:])
                axes += [""] * (3 - len(axes))
                self.writer.writerow((key, row[0], *axes, unit))
            self.counts[key] += len(rows)
            wrote = True
        if wrote:
            self.file.flush()

    def close(self):
        self.file.close()
