# EPOS2 object dictionary: the table of every value the controller exposes
# Source: EPOS2 Firmware Specification, Edition November 2017, chapter 8

from dataclasses import dataclass


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
}
