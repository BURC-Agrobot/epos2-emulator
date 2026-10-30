# EPOS2 drive state machine: the states the drive can be in
# Source: EPOS2 Firmware Specification, Edition November 2017, section 3.2


# Statusword value that reports each state, from Table 3-4, page 3-15
# The spec marks bits outside the state as "x" (not part of the state), set to 0 here
# Bit 14 is only set in Refresh and Measure Init, and bit 8 in every state but Start
STATE_STATUSWORDS: dict[str, int] = {
    "Start": 0b0000_0000_0000_0000,
    "Not Ready to Switch On": 0b0000_0001_0000_0000,
    "Switch On Disabled": 0b0000_0001_0100_0000,
    "Ready to Switch On": 0b0000_0001_0010_0001,
    "Switched On": 0b0000_0001_0010_0011,
    "Refresh": 0b0100_0001_0010_0011,
    "Measure Init": 0b0100_0001_0011_0011,
    "Operation Enable": 0b0000_0001_0011_0111,
    "Quickstop Active": 0b0000_0001_0001_0111,
    "Fault Reaction Active (disabled)": 0b0000_0001_0000_1111,
    "Fault Reaction Active (enabled)": 0b0000_0001_0001_1111,
    "Fault": 0b0000_0001_0000_1000,
}
