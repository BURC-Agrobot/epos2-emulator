# Simple motor model: an ideal axle that follows a trapezoidal velocity profile
# Source: EPOS2 Firmware Specification, Edition November 2017, section 5.3

import math
from dataclasses import dataclass

# Quadcounts per revolution: a 500-pulse encoder, the spec's own example (4 x 500)
# Spec 3.3.1, page 3-18 and 8.2.99, page 8-201
COUNTS_PER_REV = 2000
# A position this close to the goal, in quadcounts, counts as arrived
ARRIVED_COUNTS = 0.5


# Convert the spec's rpm (or rpm/s) units to quadcounts per second (or per second^2)
def rpm_to_counts(rpm: float) -> float:
    return rpm * COUNTS_PER_REV / 60


# Convert quadcounts per second back to rpm
def counts_to_rpm(counts: float) -> float:
    return counts * 60 / COUNTS_PER_REV


# The axle: the commanded position is followed exactly, with no following error
@dataclass
class Motor:
    # Current position in quadcounts
    position: float = 0.0
    # Current velocity in quadcounts per second, negative when moving backwards
    velocity: float = 0.0
    # Position being moved to, or None when there is no move in progress
    goal: int | None = None
    # Position to move to after this move, set by "finish actual positioning"
    queued_goal: int | None = None

    # Start a new move, either interrupting this one or following it
    def set_goal(self, target: int, relative: bool, immediately: bool) -> None:
        if immediately or self.goal is None:
            # A relative target adds to the position demand value (Spec 5.3.1, page 5-36)
            self.goal = round(self.position) + target if relative else target
            self.queued_goal = None
        else:
            # A following move starts where this move ends
            start = self.queued_goal if self.queued_goal is not None else self.goal
            self.queued_goal = start + target if relative else target

    # Forget any move, while the velocity ramps down in later steps (Quickstop)
    def cancel_moves(self) -> None:
        self.goal = None
        self.queued_goal = None

    # Stop at once: with the power stage off, this model does not coast
    def cut_power(self) -> None:
        self.cancel_moves()
        self.velocity = 0.0

    # Change velocity toward a wanted value, limited by the ramp rates in counts/s^2
    def _ramp(self, wanted: float, acceleration: float, deceleration: float, dt: float) -> None:
        # Speeding up uses the acceleration, and slowing or reversing the deceleration
        speeding_up = abs(wanted) > abs(self.velocity) and wanted * self.velocity >= 0
        limit = (acceleration if speeding_up else deceleration) * dt
        self.velocity += max(-limit, min(limit, wanted - self.velocity))

    # Advance the axle by dt seconds, and return True if the last move just finished
    # max_speed is in counts/s, and the ramp rates are in counts/s^2
    def step(self, dt: float, max_speed: float, acceleration: float, deceleration: float,
             halt: bool) -> bool:
        # With no move to follow, or while halted, the only thing to do is slow down
        if self.goal is None or halt:
            self._ramp(0.0, acceleration, deceleration, dt)
            self.position += self.velocity * dt
            return False
        remaining = self.goal - self.position
        # The fastest speed from which the axle can still stop at the goal
        stoppable = math.sqrt(2 * deceleration * abs(remaining))
        self._ramp(math.copysign(min(max_speed, stoppable), remaining), acceleration, deceleration, dt)
        self.position += self.velocity * dt
        remaining_after = self.goal - self.position
        # Arrived if close enough, or if this step crossed the goal at a stoppable speed
        crossed = remaining * remaining_after < 0 and abs(self.velocity) <= deceleration * dt
        if abs(remaining_after) > ARRIVED_COUNTS and not crossed:
            return False
        self.position = float(self.goal)
        self.goal = self.queued_goal
        self.queued_goal = None
        # With another move waiting, the axle keeps moving and nothing has finished
        if self.goal is not None:
            return False
        self.velocity = 0.0
        return True
