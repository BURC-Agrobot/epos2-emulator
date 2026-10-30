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

# The Statusword bits that show the state: 0-6, 8 and 14 (Spec 8.2.86, page 8-194)
STATE_BITS_MASK = 0b0100_0001_0111_1111

# Name of each Statusword bit, from Table 8-114, page 8-194
# Bits 10, 12 and 13 depend on the operating mode: these are Profile Position's names
STATUSWORD_BITS: dict[int, str] = {
    15: "Position referenced to home position",
    14: "Refresh cycle of power stage",
    13: "Following error",
    12: "Setpoint acknowledge",
    11: "Internal limit active",
    10: "Target reached",
    9: "Remote",
    8: "Offset current measured",
    7: "Warning",
    6: "Switch on disable",
    # Clear in Quickstop Active, and set in Operation Enable (Table 3-4, page 3-15)
    5: "Quickstop (set means not quick stopping)",
    4: "Voltage enabled",
    3: "Fault",
    2: "Operation enable",
    1: "Switched on",
    0: "Ready to switch on",
}


# Name the state a Statusword reports, or return None if it matches no state
def state_from_statusword(statusword: int) -> str | None:
    for state, value in STATE_STATUSWORDS.items():
        if value == statusword & STATE_BITS_MASK:
            return state
    return None


# State after each command the host can send, from Table 3-5, page 3-16
# A command missing from this table has no effect in that state
# Enable Operation passes through Refresh and Measure Init (transitions 4, 20, 21),
# which take no time here, so they are skipped and never reported
TRANSITIONS: dict[tuple[str, str], str] = {
    ("Switch On Disabled", "Shutdown"): "Ready to Switch On",
    ("Ready to Switch On", "Switch On"): "Switched On",
    # Switch On & Enable Operation has the same bits as Enable Operation (Table 3-6)
    ("Ready to Switch On", "Enable Operation"): "Operation Enable",
    ("Ready to Switch On", "Disable Voltage"): "Switch On Disabled",
    ("Ready to Switch On", "Quickstop"): "Switch On Disabled",
    ("Switched On", "Shutdown"): "Ready to Switch On",
    ("Switched On", "Enable Operation"): "Operation Enable",
    ("Switched On", "Disable Voltage"): "Switch On Disabled",
    ("Switched On", "Quickstop"): "Switch On Disabled",
    # Disable Operation has the same bits as Switch On when bit 3 is clear (Table 3-6)
    ("Operation Enable", "Switch On"): "Switched On",
    ("Operation Enable", "Shutdown"): "Ready to Switch On",
    ("Operation Enable", "Disable Voltage"): "Switch On Disabled",
    ("Operation Enable", "Quickstop"): "Quickstop Active",
    ("Quickstop Active", "Disable Voltage"): "Switch On Disabled",
    ("Quickstop Active", "Enable Operation"): "Operation Enable",
    ("Fault", "Fault Reset"): "Switch On Disabled",
}


# Name the command in a Controlword write, or return None if it holds no command
# Bit patterns from Table 3-6, page 3-17
def decode_controlword(previous: int, controlword: int) -> str | None:
    # Bit 7 selects Fault Reset, which only happens when the bit goes from 0 to 1
    if controlword & 0b1000_0000:
        return "Fault Reset" if not previous & 0b1000_0000 else None
    # Bit 1 clear (xx0x) is Disable Voltage, whatever the other bits are
    if not controlword & 0b0010:
        return "Disable Voltage"
    # Bit 2 clear (x01x) is Quickstop
    if not controlword & 0b0100:
        return "Quickstop"
    # Bit 0 clear (x110) is Shutdown
    if not controlword & 0b0001:
        return "Shutdown"
    # Only bit 3 is left: 1111 is Enable Operation and 0111 is Switch On
    return "Enable Operation" if controlword & 0b1000 else "Switch On"
