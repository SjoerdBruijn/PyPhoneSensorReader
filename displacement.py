"""Compare the two MATLAB double-integration paths on the same samples."""

from collections import deque

import numpy as np

from stream import GRAVITY, UP


class Displacement:
    def __init__(self):
        self.time = None
        self.velocity = np.zeros(3)
        self.position = np.zeros(3)
        self.filtered_velocity = np.zeros(3)
        self.filtered_position = np.zeros(3)
        self.trail = deque([(self.position, self.filtered_position)], maxlen=6000)

    def update(self, time, acceleration, rotation):
        previous, self.time = self.time, time
        if previous is None:
            return
        dt = time - previous
        if dt > 0.25:
            self.__init__()
            self.time = time
            return

        # The MATLAB demos use Euler integration, with a one-pole high-pass
        # stage on velocity and another on displacement in the filtered path.
        dv = (rotation.apply(acceleration) - GRAVITY * UP) * dt
        self.velocity += dv
        self.position = self.position + self.velocity * dt
        alpha = 0.95
        self.filtered_velocity = alpha * (self.filtered_velocity + dv)
        dp = self.filtered_velocity * dt
        self.filtered_position = alpha * (self.filtered_position + dp)
        self.trail.append((self.position, self.filtered_position))
