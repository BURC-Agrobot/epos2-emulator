# EPOS2 object dictionary: the table of every value the controller exposes
# Source: EPOS2 Firmware Specification, Edition November 2017, chapter 8

from collections.abc import Container
from dataclasses import dataclass

# Values the Modes of Operation object accepts, from Table 8-118, page 8-197
OPERATION_MODES = {7, 6, 3, 1, -1, -2, -3, -4, -5, -6}
# Values a profile speed or ramp rate accepts: above zero, up to the UNSIGNED32 limit
POSITIVE_VALUES = range(1, 2**32)


# One object dictionary entry, as described in each firmware spec table
@dataclass
class Entry:
    # Human-readable name from the spec, e.g. "Controlword"
    name: str
    # Size of the value on the bus in bytes (UNSIGNED8 = 1, UNSIGNED16 = 2, ...)
    size_bytes: int
    # True if the host may write it ("RW" in the spec), False if "RO"
    writable: bool
    # Current value held by the emulated controller
    value: int
    # True if the spec type is INTEGERn, False if UNSIGNEDn
    signed: bool = False
    # Values a write may store, or None if every value of this size is allowed
    allowed: Container[int] | None = None

    # Convert the stored value to the bit pattern sent on the bus
    def to_bus(self) -> int:
        return self.value & ((1 << (self.size_bytes * 8)) - 1)

    # Convert a bit pattern from the bus to the value to store
    def from_bus(self, raw: int) -> int:
        size_bits = self.size_bytes * 8
        # The top bit of a signed value means it counts down from the type's range
        if self.signed and raw >= 1 << (size_bits - 1):
            return raw - (1 << size_bits)
        return raw


# Every entry is found by its (index, subindex) pair, like an address
OBJECTS: dict[tuple[int, int], Entry] = {
    # Device type: CiA 402 drive profile (0x0192) on a servo drive (0x0002)
    # Spec 8.2.1, page 8-103
    (0x1000, 0x00): Entry("Device Type", 4, False, 0x00020192),
    # Error register: one bit per error class, 0 means no errors
    # Spec 8.2.2, page 8-103
    (0x1001, 0x00): Entry("Error Register", 1, False, 0),
    # Controlword: commands from the host to the drive state machine
    # Spec 8.2.85, page 8-193 (no default given, so start with no command)
    (0x6040, 0x00): Entry("Controlword", 2, True, 0x0000),
    # Statusword: the drive's report of its current state
    # Start in "Switch On Disabled" with offset current measured (bits 8, 6)
    # Spec 8.2.86, page 8-194 and Table 3-4, page 3-15
    (0x6041, 0x00): Entry("Statusword", 2, False, 0x0140),
    # Modes of Operation: the operating mode the host asks for (1 = Profile Position)
    # Spec 8.2.90, page 8-197
    (0x6060, 0x00): Entry("Modes of Operation", 1, True, 1, signed=True, allowed=OPERATION_MODES),
    # Modes of Operation Display: the operating mode in use
    # Spec 8.2.91, page 8-197
    (0x6061, 0x00): Entry("Modes of Operation Display", 1, False, 1, signed=True),
    # Position Actual Value in quadcounts (no default given, so start at zero)
    # Spec 8.2.93, page 8-198
    (0x6064, 0x00): Entry("Position Actual Value", 4, False, 0, signed=True),
    # Velocity Actual Value in rpm (no default given, so start at zero)
    # Spec 8.2.99, page 8-201
    (0x606C, 0x00): Entry("Velocity Actual Value", 4, False, 0, signed=True),
    # Target Position in quadcounts, used when the host sends a new setpoint
    # Spec 8.2.103, page 8-203
    (0x607A, 0x00): Entry("Target Position", 4, True, 0, signed=True),
    # Profile Velocity in rpm: the speed reached after the acceleration ramp
    # Spec 8.2.107, page 8-206
    (0x6081, 0x00): Entry("Profile Velocity", 4, True, 1000, allowed=POSITIVE_VALUES),
    # Profile Acceleration in rpm/s
    # Spec 8.2.108, page 8-206
    (0x6083, 0x00): Entry("Profile Acceleration", 4, True, 10000, allowed=POSITIVE_VALUES),
    # Profile Deceleration in rpm/s
    # Spec 8.2.109, page 8-206
    (0x6084, 0x00): Entry("Profile Deceleration", 4, True, 10000, allowed=POSITIVE_VALUES),
    # Quickstop Deceleration in rpm/s
    # Spec 8.2.110, page 8-207
    (0x6085, 0x00): Entry("Quickstop Deceleration", 4, True, 10000, allowed=POSITIVE_VALUES),
}
