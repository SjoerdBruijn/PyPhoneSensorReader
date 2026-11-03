"""Read bounded phyphox buffers and estimate phone-to-world orientation."""

import json
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import ProxyHandler, build_opener
from xml.etree import ElementTree

import numpy as np
from scipy.spatial.transform import Rotation


EXPERIMENT = Path(__file__).resolve().parent / "experiments" / "phone_sensors.phyphox"
SENSORS = {
    "acc": ("Accelerometer", "m/s²"),
    "gyro": ("Gyroscope", "rad/s"),
    "mag": ("Magnetometer", "µT"),
    "pressure": ("Pressure", "hPa"),
    "light": ("Light", "lx"),
    "proximity": ("Proximity", "cm"),
    "temperature": ("Temperature", "°C"),
    "humidity": ("Humidity", "%"),
    "gravity": ("Gravity", "m/s²"),
    "linear": ("Linear acceleration", "m/s²"),
}
# Map phyphox sensor types to dashboard tabs. Gravity and linear acceleration
# can also be shown when supplied by another experiment.
SENSOR_TYPES = {
    sensor.attrib["type"]: sensor.find("output[@component='t']").text.removesuffix("_t")
    for sensor in ElementTree.parse(EXPERIMENT).findall("./input/sensor")
}
SENSOR_TYPES.update(gravity="gravity", linear_acceleration="linear")
COMPONENTS = {
    key: "txyz" if key in {"acc", "gyro", "mag", "gravity", "linear"} else "tx"
    for key in SENSORS
}
GRAVITY = 9.81
UP = np.array([0.0, 0.0, 1.0])


def phone_address(address):
    address = address.strip()
    if "://" not in address:
        address = "http://" + address
    parts = urlsplit(address)
    if parts.scheme != "http" or not parts.hostname or parts.username is not None:
        raise ValueError("Enter the HTTP address shown by phyphox.")
    # Accessing port checks for malformed or out-of-range port numbers.
    _ = parts.port
    return f"http://{parts.netloc}"


class Phone:
    def __init__(self, address):
        self.address = phone_address(address)
        self.opener = build_opener(ProxyHandler({}))
        self.channels = {}
        self.unbounded = set()
        self.cursor = {}
        self.session = None

    def get(self, path):
        with self.opener.open(self.address + path, timeout=2) as response:
            return json.load(response)

    def configure(self):
        config = self.get("/config")
        sizes = {buffer["name"]: buffer["size"] for buffer in config["buffers"]}
        for sensor in config["inputs"]:
            key = SENSOR_TYPES.get(sensor["source"])
            if key is None:
                continue
            outputs = {axis: name for item in sensor["outputs"] for axis, name in item.items()}
            if not all(outputs.get(axis) in sizes for axis in COMPONENTS[key]):
                continue
            channels = {axis: outputs[axis] for axis in COMPONENTS[key]}
            self.channels[key] = channels
            if any(sizes[name] == 0 for name in channels.values()):
                self.unbounded.add(key)
        if not self.channels:
            raise ValueError("This phyphox experiment exposes no supported timestamped sensors.")

    def buffers(self, parameters):
        return self.get("/get?" + urlencode(parameters))

    def poll(self):
        if not self.channels:
            self.configure()

        # Probe the latest time on unbounded recordings. This avoids downloading
        # the entire recording on connect and detects when the phone clears it.
        time_names = {key: self.channels[key]["t"] for key in self.unbounded}
        if time_names:
            latest = self.buffers({name: "" for name in time_names.values()})
            for key, name in time_names.items():
                values = latest["buffer"].get(name, {}).get("buffer", [])
                t = values[-1] if values else None
                if t is None:
                    self.cursor.pop(key, None)
                elif key not in self.cursor or t < self.cursor[key]:
                    self.cursor[key] = t - 1.0

        parameters = {}
        for key, channels in self.channels.items():
            timestamp = channels["t"]
            for axis, name in channels.items():
                if key not in self.unbounded:
                    parameters[name] = "full"
                else:
                    # At most a few seconds of catch-up after a dropped connection.
                    values = latest["buffer"].get(timestamp, {}).get("buffer", [])
                    t = values[-1] if values and values[-1] is not None else 0
                    threshold = max(self.cursor.get(key, t - 1.0), t - 3.0)
                    parameters[name] = str(threshold) if axis == "t" else f"{threshold}|{timestamp}"

        response = self.buffers(parameters)
        status = response["status"]
        session = status["session"]
        if self.session is not None and session != self.session:
            self.channels.clear()
            self.unbounded.clear()
            self.cursor.clear()
            self.session = None
            return session, status["measuring"], {}
        self.session = session
        buffers = response["buffer"]
        samples = {}
        for key, channels in self.channels.items():
            columns = [buffers.get(name, {}).get("buffer", []) for name in channels.values()]
            # A complete sample needs its timestamp and all its components.
            if len({len(column) for column in columns}) != 1:
                continue
            rows = np.column_stack(columns).astype(float)
            rows = rows[np.isfinite(rows).all(axis=1)]
            samples[key] = rows
            if len(rows) and key in self.unbounded:
                self.cursor[key] = rows[-1, 0]
        return session, status["measuring"], samples


def demo(elapsed):
    """Simulated foot swings with known displacement and stationary stances."""
    t = np.arange(max(0, int(elapsed * 100) - 255), int(elapsed * 100) + 1) / 100
    duration = 0.75
    phase = (t % 1.4 - 0.35) / duration
    moving = (phase > 0) & (phase < 1)
    u = np.clip(phase, 0, 1)
    angle = 2 * np.pi * u
    tilt = 0.2 * np.sin(np.pi * u) ** 2
    pose = Rotation.from_euler("X", tilt)
    gyro = np.zeros((len(t), 3))
    gyro[:, 0] = moving * 0.2 * np.pi * np.sin(angle) / duration
    specific_force = np.zeros_like(gyro)
    specific_force[:, 0] = moving * 0.65 * 60 * u * (1-u) * (1-2*u) / duration**2
    specific_force[:, 2] = GRAVITY + moving * 0.09 * (2*np.pi/duration)**2 * (
        np.cos(angle) - np.cos(2*angle)
    ) / 2
    samples = {
        "acc": np.column_stack((t, pose.inv().apply(specific_force))),
        "gyro": np.column_stack((t, gyro)),
        "mag": np.column_stack((t, pose.inv().apply([25, 0, -40]))),
        "pressure": np.column_stack((t, 1013 + 0.15 * np.sin(t))),
    }
    return "demo", True, samples


def madgwick_gradient(q, reference, measurement):
    """Gradient of half the squared direction error for one reference vector."""
    w, v = q[0], q[1:]
    x, y, z = reference
    skew = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    cross = skew @ v
    projection = v @ reference
    predicted = reference + 2 * (w * cross + v * projection - reference * (v @ v))
    jacobian = 2 * np.column_stack((
        cross,
        w * skew + np.outer(v, reference) + projection * np.eye(3)
        - 2 * np.outer(reference, v),
    ))
    return jacobian.T @ (predicted - measurement)


class Orientation:
    """Madgwick IMU/MARG updates on the gyroscope's sample clock."""

    def __init__(self, gain):
        self.gain = gain
        self.use_magnetometer = True
        self.time = None
        self.q = None  # Hamilton quaternion, scalar first, phone to world
        self.mode = "Waiting for accelerometer and gyroscope"

    @property
    def rotation(self):
        return Rotation.from_quat(np.roll(self.q, -1))

    def update(self, samples, on_sample=None):
        acc, gyro = samples["acc"], samples["gyro"]
        mag = samples["mag"]
        if not len(acc) or not len(gyro):
            return
        for t, *omega in gyro:
            if self.time is not None and t <= self.time:
                continue
            # Wait for accelerometer data before interpolating to the gyro clock.
            if t > acc[-1, 0]:
                break
            index = np.searchsorted(acc[:, 0], t)
            if t < acc[0, 0]:
                continue
            if acc[index, 0] != t and acc[index, 0] - acc[index - 1, 0] > 0.2:
                continue
            acceleration = np.array([np.interp(t, acc[:, 0], axis) for axis in acc[:, 1:].T])
            norm = np.linalg.norm(acceleration)
            if norm < 1e-6:
                continue
            up = acceleration / norm

            mag_index = np.searchsorted(mag[:, 0], t, side="right") - 1
            field = None
            if self.use_magnetometer and mag_index >= 0 and t - mag[mag_index, 0] <= 0.25:
                magnetic = mag[mag_index, 1:]
                magnitude = np.linalg.norm(magnetic)
                if magnitude > 1e-6:
                    field = magnetic / magnitude
                    if np.linalg.norm(np.cross(up, field)) < 1e-3:
                        field = None

            omega = np.asarray(omega)
            if self.time is None or t - self.time > 0.25:
                if field is None:
                    axis = np.cross(up, UP)
                    sine = np.linalg.norm(axis)
                    cosine = up @ UP
                    if sine > 1e-10:
                        initial = Rotation.from_rotvec(axis * (np.arctan2(sine, cosine) / sine))
                    else:
                        initial = Rotation.from_rotvec([np.pi if cosine < 0 else 0, 0, 0])
                else:
                    west = np.cross(up, field)
                    west /= np.linalg.norm(west)
                    north = np.cross(west, up)
                    initial = Rotation.from_matrix([north, west, up])
                self.q = np.roll(initial.as_quat(), 1)
            else:
                self.step(omega, up, field, t - self.time)
            self.time = t
            self.mode = ("Accelerometer + gyroscope + magnetometer"
                         if field is not None else "Accelerometer + gyroscope · yaw may drift")
            if on_sample is not None:
                on_sample(t, acceleration, omega, self.rotation)

    def step(self, omega, up, field, dt):
        q = self.q
        gradient = madgwick_gradient(q, UP, up)
        if field is not None:
            world_field = self.rotation.apply(field)
            reference = np.array([np.hypot(*world_field[:2]), 0, world_field[2]])
            gradient += madgwick_gradient(q, reference, field)
        w, v = q[0], q[1:]
        rate = 0.5 * np.r_[-v @ omega, w * omega + np.cross(v, omega)]
        norm = np.linalg.norm(gradient)
        if norm > 1e-10:
            rate -= self.gain * gradient / norm
        q = q + rate * dt
        self.q = q / np.linalg.norm(q)
