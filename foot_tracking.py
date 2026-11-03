"""Stride-by-stride inertial foot tracking with zero-velocity updates."""

from collections import deque

import numpy as np
from scipy.integrate import cumulative_trapezoid

from stream import GRAVITY, UP


class FootTracker:
    """Track from one stationary stance to the next on the IMU sample clock."""

    def __init__(self):
        self.trail = deque([np.zeros(3)], maxlen=6000)
        self.swing = []
        self.last_sample = None
        self.quiet_since = None
        self.position = np.zeros(3)
        self.state = "Hold the foot still to start"
        self.ready = False
        self.strides = 0
        self.last_stride = None
        self.level_ground = False

    def update(self, time, acceleration, omega, rotation):
        world_acc = rotation.apply(acceleration) - GRAVITY * UP
        sample = (time, world_acc)
        previous = self.last_sample
        self.last_sample = sample
        if previous is None or time - previous[0] > 0.25:
            self.wait_for_stance("Hold still to re-anchor" if previous else self.state)

        acc_error = abs(np.linalg.norm(acceleration) - GRAVITY)
        rate = np.linalg.norm(omega)
        quiet = acc_error < 0.65 and rate < 0.4
        moving = acc_error > 0.8 or rate > 0.5
        if quiet:
            if self.quiet_since is None:
                self.quiet_since = time
        else:
            self.quiet_since = None

        if not self.ready:
            if self.quiet_since is not None and time - self.quiet_since >= 0.20:
                self.ready = True
                self.state = "Stance · velocity zero"
            return

        if not self.swing:
            if moving and previous is not None:
                self.swing = [previous, sample]
                self.state = "Swing · provisional path"
            return

        self.swing.append(sample)
        if self.quiet_since is not None and time - self.quiet_since >= 0.08:
            # The first quiet sample marks the endpoint. Later samples only
            # confirm stance and do not extend the integrated swing.
            swing = [point for point in self.swing if point[0] <= self.quiet_since]
            if swing[-1][0] - swing[0][0] >= 0.12:
                path = self.integrate(swing, corrected=True)
                self.last_stride = float(np.linalg.norm(path[-1, :2] - self.position[:2]))
                self.position = path[-1].copy()
                self.trail.extend(path[1:])
                self.strides += 1
            self.swing.clear()
            self.state = "Stance · velocity zero"
        elif time - self.swing[0][0] > 3:
            self.wait_for_stance("No stance detected · hold still to re-anchor")

    def wait_for_stance(self, message):
        self.swing.clear()
        self.quiet_since = None
        self.ready = False
        self.state = message

    def integrate(self, samples, corrected):
        times = np.array([time for time, _ in samples])
        acceleration = np.array([acc for _, acc in samples])
        velocity = cumulative_trapezoid(acceleration, times, axis=0, initial=0)
        if corrected:
            fraction = (times - times[0]) / (times[-1] - times[0])
            velocity -= fraction[:, None] * velocity[-1].copy()
            if self.level_ground:
                # Cancel the vertical displacement with a smooth velocity
                # correction that also vanishes at both stance endpoints.
                weight = np.sin(np.pi * fraction) ** 2
                height = cumulative_trapezoid(velocity[:, 2], times)[-1]
                area = cumulative_trapezoid(weight, times)[-1]
                if area > 1e-10:
                    velocity[:, 2] -= height * weight / area
        return self.position + cumulative_trapezoid(velocity, times, axis=0, initial=0)

    @property
    def provisional(self):
        return self.integrate(self.swing, corrected=False) if self.swing else self.position[None, :]
