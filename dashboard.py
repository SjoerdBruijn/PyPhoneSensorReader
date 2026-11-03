"""Run a live phyphox dashboard, or use --demo without a phone."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from itertools import product
from pathlib import Path
import sys
from time import monotonic

import numpy as np
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QStackedWidget, QTabWidget,
    QVBoxLayout, QWidget,
)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

from displacement import Displacement
from foot_tracking import FootTracker
from measurement import Measurement
from stream import COMPONENTS, SENSORS, Orientation, Phone, demo


class SensorPlot(FigureCanvasQTAgg):
    def __init__(self, key):
        super().__init__(Figure(layout="constrained"))
        self.axes = self.figure.add_subplot()
        self.title, unit = SENSORS[key]
        self.axes.set(title=self.title, xlabel="Experiment time (s)", ylabel=unit)
        self.axes.grid(alpha=0.25)
        self.lines = [
            self.axes.plot([], [], label=axis.upper())[0]
            for axis in COMPONENTS[key] if axis != "t"
        ]
        if len(self.lines) > 1:
            self.axes.legend(loc="upper right")
        self.message = self.axes.text(
            0.5, 0.5, "", ha="center", transform=self.axes.transAxes,
        )

    def render(self, rows, measuring):
        intervals = np.diff(rows[-101:, 0])
        intervals = intervals[intervals > 0]
        if len(intervals):
            rate = f"{1 / np.median(intervals):.1f} Hz"
            if not measuring:
                rate = f"last measured {rate}"
        else:
            rate = "sample rate pending"
        self.axes.set_title(f"{self.title} · {rate}")

        for column, line in enumerate(self.lines, start=1):
            line.set_data(rows[:, 0], rows[:, column])
        if len(rows):
            self.axes.relim()
            self.axes.autoscale_view()
            self.message.set_text("")
        else:
            self.message.set_text("No readings yet · sensor may be unavailable" if measuring
                                  else "No readings · press Play in phyphox")
        self.draw_idle()


class PhoneBox(Poly3DCollection):
    def __init__(self, axes, scale=1, screen_color="#46a5df"):
        x, y, z = np.array([0.48, 0.95, 0.08]) * scale
        self.vertices = np.array(list(product((-x, x), (-y, y), (-z, z))))
        self.faces = np.array([
            [0, 2, 6, 4], [1, 5, 7, 3], [0, 1, 3, 2],
            [4, 6, 7, 5], [0, 4, 5, 1], [2, 3, 7, 6],
        ])
        super().__init__(
            self.vertices[self.faces],
            facecolors=["#293849", screen_color, "#304f70", "#304f70", "#304f70", "#ffab40"],
            edgecolors="#1c2937", linewidths=1,
        )
        axes.add_collection3d(self)

    def place(self, rotation, position):
        vertices = rotation.apply(self.vertices) + position
        self.set_verts(vertices[self.faces])
        return vertices


class PhonePlot(FigureCanvasQTAgg):
    def __init__(self):
        super().__init__(Figure(layout="constrained"))
        axes = self.figure.add_subplot(projection="3d")
        axes.set(xlim=(-1.4, 1.4), ylim=(-1.4, 1.4), zlim=(-1.4, 1.4),
                 xlabel="X", ylabel="Y", zlabel="Up")
        axes.set_box_aspect((1, 1, 1))
        axes.view_init(elev=23, azim=-55)
        self.box = PhoneBox(axes)
        self.axes = axes

    def render(self, estimate):
        ready = estimate.q is not None
        self.box.set_visible(ready)
        if ready:
            self.box.place(estimate.rotation, np.zeros(3))
        self.axes.set_title(estimate.mode)
        self.draw_idle()


class DisplacementPlot(FigureCanvasQTAgg):
    def __init__(self):
        super().__init__(Figure(layout="constrained"))
        self.axes = self.figure.add_subplot(projection="3d")
        self.axes.set(xlabel="World X (m)", ylabel="World Y (m)", zlabel="Up (m)")
        self.axes.set_box_aspect((1, 1, 1))
        self.lines = [
            self.axes.plot([], [], [], color=color, label=label)[0]
            for color, label in (("#ce731f", "Unfiltered"), ("#146ea3", "High-pass filtered"))
        ]

        # Use phone-sized geometry because the displacement axes are in metres.
        self.boxes = [PhoneBox(self.axes, scale=0.075, screen_color=line.get_color())
                      for line in self.lines]

    def render(self, displacement, mode, estimate):
        trail = np.asarray(displacement.trail)
        selected = (mode in (1, 3), mode in (2, 3))
        bounds = [trail[:, selected].reshape(-1, 3)]
        rotation = estimate.rotation if estimate.q is not None else None
        for i, (line, box) in enumerate(zip(self.lines, self.boxes)):
            line.set_visible(selected[i])
            line.set_data_3d(*trail[:, i].T)
            box.set_visible(selected[i] and rotation is not None)
            if box.get_visible():
                bounds.append(box.place(rotation, trail[-1, i]))
        points = np.vstack(bounds)
        low, high = points.min(axis=0), points.max(axis=0)
        center = (low + high) / 2
        radius = max(0.03, float(max(high - low)) * 0.56)
        self.axes.set(xlim=(center[0] - radius, center[0] + radius),
                      ylim=(center[1] - radius, center[1] + radius),
                      zlim=(center[2] - radius, center[2] + radius))
        self.axes.legend(handles=[line for line in self.lines if line.get_visible()],
                         loc="upper left")
        self.draw_idle()


class MeasurementTab(QWidget):
    def __init__(self, start, stop):
        super().__init__()
        self.base = QLineEdit("measurement")
        self.folder = QLineEdit(str(Path(__file__).resolve().parent / "measurements"))
        browse = QPushButton("Choose…")
        browse.clicked.connect(self.choose_folder)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(browse)
        self.settings = QWidget()
        form = QFormLayout(self.settings)
        form.addRow("Base filename", self.base)
        form.addRow("Save folder", folder_row)

        self.sensors = {}
        sensor_grid = QGridLayout()
        for i, (key, (title, _)) in enumerate(SENSORS.items()):
            checkbox = QCheckBox(title)
            checkbox.setChecked(key in ("acc", "gyro", "mag"))
            self.sensors[key] = checkbox
            sensor_grid.addWidget(checkbox, i // 3, i % 3)
        form.addRow("Sensors", sensor_grid)

        self.start = QPushButton("Start")
        self.stop = QPushButton("Stop")
        self.start.clicked.connect(start)
        self.stop.clicked.connect(lambda: stop())
        buttons = QHBoxLayout()
        buttons.addWidget(self.start)
        buttons.addWidget(self.stop)
        buttons.addStretch()
        self.status = QLabel("Connect and start streaming before recording.")
        self.status.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addWidget(self.settings)
        layout.addLayout(buttons)
        layout.addWidget(self.status)
        layout.addStretch()
        self.set_recording(False)

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose measurement folder", self.folder.text())
        if folder:
            self.folder.setText(folder)

    def set_recording(self, active):
        self.settings.setEnabled(not active)
        self.start.setEnabled(not active)
        self.stop.setEnabled(active)


class FootPlot(QWidget):
    def __init__(self, reset):
        super().__init__()
        self.level_ground = QCheckBox("Level ground")
        self.reset_button = QPushButton("Reset path")
        self.reset_button.clicked.connect(reset)
        self.status = QLabel()
        controls = QHBoxLayout()
        controls.addWidget(self.reset_button)
        controls.addWidget(self.level_ground)
        controls.addStretch()

        self.canvas = FigureCanvasQTAgg(Figure(layout="constrained"))
        self.axes = self.canvas.figure.add_subplot(projection="3d")
        self.axes.set(xlabel="World X (m)", ylabel="World Y (m)", zlabel="Up (m)")
        self.axes.set_box_aspect((1, 1, 1))
        self.confirmed, = self.axes.plot([], [], [], color="#146ea3", label="Corrected path")
        self.swing, = self.axes.plot([], [], [], "--", color="#ce731f", label="Provisional swing")
        self.frame = [self.axes.plot([], [], [], color=color, linewidth=2)[0]
                      for color in ("#cc4040", "#3a9b51", "#437bd3")]
        self.axes.legend(loc="upper left")

        layout = QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(self.status)
        layout.addWidget(self.canvas, 1)

    def render(self, tracker, estimate):
        trail = np.asarray(tracker.trail)
        swing = tracker.provisional
        self.confirmed.set_data_3d(*trail.T)
        self.swing.set_data_3d(*swing.T)
        current = swing[-1]
        endpoints = (current + estimate.rotation.apply(0.12 * np.eye(3))
                     if estimate.q is not None else np.tile(current, (3, 1)))
        for line, endpoint in zip(self.frame, endpoints):
            line.set_data_3d(*np.vstack((current, endpoint)).T)
        points = np.vstack((trail, swing, endpoints))
        low, high = points.min(axis=0), points.max(axis=0)
        center = (low + high) / 2
        radius = max(0.35, float(max(high - low)) * 0.56)
        self.axes.set(xlim=(center[0] - radius, center[0] + radius),
                      ylim=(center[1] - radius, center[1] + radius),
                      zlim=(center[2] - radius, center[2] + radius))
        last = (f" · Last horizontal stride {tracker.last_stride:.2f} m"
                if tracker.last_stride is not None else "")
        self.status.setText(f"{tracker.state} · {tracker.strides} completed strides{last}")
        self.canvas.draw_idle()


class Dashboard(QWidget):
    def __init__(self, args):
        super().__init__()
        self.setWindowTitle("phyphox · Phone Sensor Stream")
        self.resize(1100, 680)
        self.gain = args.gain
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.pending = None
        self.source = None
        self.recording = None
        self.address = QLineEdit(args.address or "")
        self.address.setPlaceholderText("Address shown in phyphox, e.g. http://192.168.1.10:8080")
        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self.toggle_connection)
        self.address.returnPressed.connect(self.toggle_connection)
        self.demo_button = QPushButton("Demo")
        self.demo_button.clicked.connect(lambda: self.start(demo))
        self.status = QLabel("Enter the remote access address from phyphox, or try Demo.")
        self.status.setWordWrap(True)
        controls = QHBoxLayout()
        controls.addWidget(self.address, 1)
        controls.addWidget(self.connect_button)
        controls.addWidget(self.demo_button)

        self.tabs = QTabWidget()
        self.measurement_tab = MeasurementTab(self.start_measurement, self.stop_measurement)
        self.tabs.addTab(self.measurement_tab, "Measurement")
        self.phone_plot = PhonePlot()
        self.displacement_plot = DisplacementPlot()
        self.orientation_views = QStackedWidget()
        self.orientation_views.addWidget(self.phone_plot)
        self.orientation_views.addWidget(self.displacement_plot)
        self.use_magnetometer = QCheckBox("Use magnetometer")
        self.use_magnetometer.setChecked(True)
        self.use_magnetometer.toggled.connect(self.set_magnetometer)
        self.orientation_mode = QComboBox()
        self.orientation_mode.addItems([
            "Phone orientation", "Double integration", "Double integration with high-pass",
            "Compare integration paths",
        ])
        self.orientation_mode.currentIndexChanged.connect(self.render)
        reset_displacement = QPushButton("Reset displacement")
        reset_displacement.clicked.connect(self.reset_displacement)
        orientation_controls = QHBoxLayout()
        orientation_controls.addWidget(self.use_magnetometer)
        orientation_controls.addWidget(self.orientation_mode)
        orientation_controls.addWidget(reset_displacement)
        orientation_controls.addStretch()
        self.orientation_tab = QWidget()
        orientation_layout = QVBoxLayout(self.orientation_tab)
        orientation_layout.addLayout(orientation_controls)
        orientation_layout.addWidget(self.orientation_views, 1)
        self.plots = {key: SensorPlot(key) for key in SENSORS}
        for key, plot in self.plots.items():
            self.tabs.addTab(plot, SENSORS[key][0])
        self.tabs.addTab(self.orientation_tab, "Orientation")
        self.foot_plot = FootPlot(self.reset_foot)
        self.tabs.addTab(self.foot_plot, "Foot tracking")
        self.foot_plot.level_ground.toggled.connect(self.set_level_ground)
        self.tabs.currentChanged.connect(self.render)

        layout = QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(self.status)
        layout.addWidget(self.tabs, 1)
        self.reset()
        self.render()
        self.timer = QTimer(self, interval=100)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        if args.demo:
            self.start(demo)
        elif args.address:
            self.toggle_connection()

    def reset(self):
        self.session = None
        self.measuring = False
        self.estimate = Orientation(self.gain)
        self.estimate.use_magnetometer = self.use_magnetometer.isChecked()
        self.displacement = Displacement()
        self.foot = FootTracker()
        self.foot.level_ground = self.foot_plot.level_ground.isChecked()
        self.history = {key: np.empty((0, len(channels))) for key, channels in COMPONENTS.items()}

    def set_magnetometer(self, enabled):
        self.estimate.use_magnetometer = enabled

    def reset_displacement(self):
        self.displacement = Displacement()
        self.render()

    def process_motion(self, time, acceleration, omega, rotation):
        self.foot.update(time, acceleration, omega, rotation)
        self.displacement.update(time, acceleration, rotation)

    def start_measurement(self):
        tab = self.measurement_tab
        if self.source is None or not self.measuring:
            tab.status.setText("Start the sensor stream before recording.")
            return
        sensors = [key for key, checkbox in tab.sensors.items() if checkbox.isChecked()]
        folder = tab.folder.text().strip()
        if not folder:
            tab.status.setText("Choose a save folder.")
            return
        try:
            self.recording = Measurement(folder, tab.base.text(), sensors)
        except (OSError, ValueError) as error:
            tab.status.setText(f"Could not start recording · {error}")
            return
        self.recording_start = max(
            (rows[-1, 0] for rows in self.history.values() if len(rows)), default=-np.inf,
        )
        tab.set_recording(True)
        tab.status.setText(f"Recording to {self.recording.path}")

    def stop_measurement(self, reason=""):
        if self.recording is None:
            return
        recording, self.recording = self.recording, None
        tab = self.measurement_tab
        tab.set_recording(False)
        try:
            recording.close()
        except OSError as error:
            tab.status.setText(f"Could not finish {recording.path} · {error}")
            return
        counts = ", ".join(f"{SENSORS[key][0]} {count}" for key, count in recording.counts.items())
        prefix = f"{reason} · " if reason else ""
        tab.status.setText(f"{prefix}Saved {recording.path} · {counts}")

    def reset_foot(self):
        self.foot = FootTracker()
        self.foot.level_ground = self.foot_plot.level_ground.isChecked()
        self.render()

    def set_level_ground(self, enabled):
        self.foot.level_ground = enabled

    def start(self, source):
        self.stop_measurement("Source changed")
        self.reset()
        self.source = source
        self.started = monotonic()
        self.connect_button.setText("Disconnect")
        self.address.setEnabled(False)
        self.status.setText("Starting demo…" if source is demo else "Connecting to phyphox…")
        self.render()

    def toggle_connection(self):
        if self.source is not None:
            self.stop_measurement("Disconnected")
            self.source = None
            self.connect_button.setText("Connect")
            self.address.setEnabled(True)
            self.status.setText("Disconnected.")
            self.reset()
            self.render()
            return
        try:
            self.start(Phone(self.address.text()))
        except ValueError as error:
            self.status.setText(str(error))

    def tick(self):
        if self.pending is not None:
            source, future = self.pending
            if not future.done():
                return
            self.pending = None
            if source is self.source:
                try:
                    frame = future.result()
                except (OSError, ValueError, KeyError, TypeError) as error:
                    self.status.setText(f"Stream interrupted · {error}")
                else:
                    self.accept(*frame)
        if self.source is demo:
            self.accept(*demo(monotonic() - self.started))
        elif self.source is not None:
            self.pending = self.source, self.pool.submit(self.source.poll)

    def accept(self, session, measuring, samples):
        if session != self.session or any(
            len(self.history[key]) and len(rows) and rows[-1, 0] < self.history[key][-1, 0]
            for key, rows in samples.items()
        ):
            self.stop_measurement("Experiment restarted")
            self.reset()
        self.session, self.measuring = session, measuring
        new_samples = {}
        for key, rows in samples.items():
            history = self.history[key]
            if len(history):
                rows = rows[rows[:, 0] > history[-1, 0]]
            if len(rows):
                new_samples[key] = rows
                history = np.concatenate((history, rows))
                self.history[key] = history[history[:, 0] >= history[-1, 0] - 10][-2500:]
        if self.recording is not None:
            try:
                self.recording.write({
                    key: rows[rows[:, 0] > self.recording_start]
                    for key, rows in new_samples.items()
                })
            except OSError as error:
                self.stop_measurement(f"Recording interrupted: {error}")
        self.estimate.update(self.history, on_sample=self.process_motion)
        if self.source is demo:
            status = "Demo · simulated phone motion"
        elif measuring:
            status = f"Streaming from {self.source.address}"
        else:
            status = "Connected · press Play in phyphox to stream"
        self.status.setText(status)
        self.render()

    def render(self):
        current = self.tabs.currentWidget()
        if current is self.orientation_tab:
            mode = self.orientation_mode.currentIndex()
            self.orientation_views.setCurrentIndex(int(mode != 0))
            if mode:
                self.displacement_plot.render(self.displacement, mode, self.estimate)
            else:
                self.phone_plot.render(self.estimate)
        elif current is self.foot_plot:
            current.render(self.foot, self.estimate)
        elif current is not self.measurement_tab:
            key = next(key for key, plot in self.plots.items() if plot is current)
            current.render(self.history[key], self.measuring)

    def closeEvent(self, event):
        self.stop_measurement()
        self.timer.stop()
        self.pool.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(event)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--address", help="HTTP address shown by phyphox")
    source.add_argument("--demo", action="store_true", help="Run with simulated sensors")
    parser.add_argument("--gain", type=float, default=0.04, help="Madgwick correction gain")
    args = parser.parse_args()
    if not np.isfinite(args.gain) or args.gain < 0:
        parser.error("--gain must be finite and nonnegative")
    app = QApplication(sys.argv[:1])
    window = Dashboard(args)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
