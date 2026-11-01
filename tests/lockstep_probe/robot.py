"""Probe robot for the lockstep extension tests: reports its loop count and clock over a SimDevice."""

import os
import time

import hal
import wpilib

_D = hal.SimDevice.Direction


class MyRobot(wpilib.TimedRobot):
    def __init__(self):
        # PROBE_INIT_DELAY: slow constructor work before TimedRobot creates its notifier
        time.sleep(float(os.environ.get("PROBE_INIT_DELAY", "0")))
        super().__init__()

    def robotInit(self):
        # "Probe" has no "Type:" prefix, so the ws client relays it as {"type": "SimDevice", "device": "Probe"}
        self.dev = hal.SimDevice("Probe")
        self.init_us = self.dev.createLong("init_us", _D.kOutput, wpilib.RobotController.getFPGATime())
        self.loop_us = self.dev.createLong("loop_us", _D.kOutput, 0)
        self.loops = self.dev.createLong("loops", _D.kOutput, 0)
        self.fast = self.dev.createLong("fast", _D.kOutput, 0)
        self.addPeriodic(self._fast, 0.005)

    def _fast(self):
        self.fast.set(self.fast.get() + 1)

    def robotPeriodic(self):
        if self.loops.get() + 1 == int(os.environ.get("PROBE_CRASH_AT_LOOP", "0")):
            raise RuntimeError("probe robot crashed on purpose")
        # loop_us first: by the time scrimmage sees `loops` change, this loop's clock reading is already there
        self.loop_us.set(wpilib.RobotController.getFPGATime())
        self.loops.set(self.loops.get() + 1)
