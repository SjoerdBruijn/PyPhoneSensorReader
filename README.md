# PyPhoneSensorReader

PyPhoneSensorReader is a Python desktop dashboard for live sensor data from
phyphox on iPhone or Android. The dashboard plots the most recent 10 seconds of readings from each available
sensor. Drag the 3D view to change the camera angle. The blue face is the phone
screen and the orange edge is the top of the phone in the orientation view.

## Set up and try the demo

In a terminal opened in this folder, using Python 3.10 or later:

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python dashboard.py --demo
```

On Windows, use `.venv\Scripts\python` in the last two commands.

## Connect your phone

1. Install phyphox on your phone from the App Store or Google Play.
   [Official download links](https://phyphox.org/download/)
2. Send [phone_sensors.phyphox](experiments/phone_sensors.phyphox) to your phone using a
   file transfer method such as email or cloud storage. On your phone, open
   the file with phyphox. If it opens in a preview, use **Share** and choose
   **phyphox** or **Copy to phyphox**. Open **Phone Sensor Stream** in the app.
   [phyphox experiment import instructions](https://phyphox.org/docs/transferring-experiments/)
3. Put your phone and computer on the same local network. In the experiment,
   open the three-dot menu, enable **Allow remote access**, and copy the full
   HTTP address shown, including the port if one is listed.
4. Set the phone down briefly and press **Play** in phyphox. Keep the
   experiment open.
5. On the computer, run `.venv/bin/python dashboard.py` (on Windows,
   `.venv\Scripts\python dashboard.py`). Paste the address into the dashboard
   and click **Connect**. Open a sensor tab to check that readings arrive.

You can also use an existing phyphox experiment. For 3D orientation
and foot tracking, use an experiment that records the accelerometer with
gravity and the gyroscope together. A magnetometer adds a heading reference.

If the connection fails, try opening the displayed address in a browser on
the same computer. A network that isolates devices may require a different
Wi-Fi network or a personal hotspot.

## Orientation

The dashboard uses the accelerometer **with gravity**, gyroscope and, when
available, magnetometer readings from the phone. Madgwick AHRS uses the
gyroscope's measurement timestamps for integration. The world Z axis points
up and X points toward magnetic north when a usable magnetic field is
available. With accelerometer and gyroscope only, roll and pitch are
gravity-referenced and yaw can drift. Strong acceleration and nearby
magnetic objects can affect the estimate. Optional sensors without readings
have empty tabs.

[phyphox remote interface](https://phyphox.org/docs/remote-interface/) ·
[phyphox sensor configuration](https://phyphox.org/docs/file-format/input/) ·
[Madgwick algorithm equations](https://ahrs.readthedocs.io/en/latest/filters/madgwick.html)

## Foot tracking

Secure the phone firmly to one shoe. Open the last tab, **Foot tracking**,
and click **Reset path** while the foot is still. Wait for **Stance · velocity
zero**, then walk. The tab counts strides of the instrumented foot. Each stride
runs from one stationary stance to the next on that same foot.

The dashed orange path is the provisional swing. When the tracker confirms
stance, it forces the final velocity to zero and redraws the completed stride
in blue. The colored axes show the phone orientation at the current position.
Turn on **Level ground** to constrain each newly completed stride to end at its
starting height. Leave it off for stairs or uneven ground.

The tracker uses sensor timestamps throughout, requires a short quiet interval to confirm
stance, and discards an unfinished swing after a stream gap. The estimate
depends on a firm mount, detectable stationary periods, and good orientation
data. Position and heading can drift over successive strides.

Run the dashboard with `--demo` and select **Foot tracking** to see a simulated
walk. Each simulated stride travels 0.65 m horizontally.

[Zero-velocity detection for foot-mounted inertial navigation](https://ieeexplore.ieee.org/document/5523938/)

## Compare orientation and displacement

In **Orientation**, clear **Use magnetometer** to continue from the current
orientation with accelerometer and gyroscope only. Heading can drift in this
mode. The switch also applies to the orientation used in **Foot tracking**.

Choose **Double integration**, **Double integration with high-pass**, or
**Compare integration paths**. Both paths rotate acceleration to world
coordinates, subtract gravity, and integrate using the phone's timestamps.
The filtered path applies a first-order high-pass filter
(coefficient 0.95 per sample) after each integration. It reduces slow drift
and also attenuates slow or sustained motion. These paths use no zero-velocity
updates. The phone box moves along each visible path with the current
orientation. In comparison mode, each box matches the color of its trace.
**Reset displacement** gives both paths a common starting point.

## Record a measurement

Open **Measurement** while the phone is streaming. Set a base filename and
save folder, choose the sensors, and click **Start**. Click **Stop** to finish
the file and see how many samples were saved per sensor. The default folder
is `measurements` beside the dashboard. Files are numbered, for example
`walk_001.csv`, `walk_002.csv`. Existing files are preserved.

Each CSV row has a sensor key, the original experiment timestamp in seconds,
up to three axis values, and a unit. Sensors are saved at their own sample
times. Single-axis sensors leave Y and Z blank. Selected sensors without
readings have zero saved samples. Changing streams, restarting the phone
experiment, or closing the dashboard finishes the current recording.

## Sample rate

Each sensor tab shows its estimated sample rate in Hz, calculated from the
median of up to 100 recent intervals between phone timestamps. This is the
sensor rate, separate from how often the dashboard polls the phone. With
fewer than two timestamps, the rate is pending.

The sampling rate is configured in the phyphox experiment. To change it in
the bundled experiment, edit the relevant `rate="…"` values in
[phone_sensors.phyphox](experiments/phone_sensors.phyphox) and reopen the updated file on
your phone. The bundled experiment requests 100 Hz for the accelerometer
and gyroscope, 50 Hz for the magnetometer, and 20 Hz for the other sensors.
The device can deliver a different rate. Some sensors report when their
reading changes. For a different experiment, change its
own sensor configuration.

[phyphox sensor rate settings](https://phyphox.org/docs/file-format/input/)

## License

The Python dashboard, bundled experiment, and this README are available under
the [MIT License](LICENSE).

I vibe coded this with Codex.
