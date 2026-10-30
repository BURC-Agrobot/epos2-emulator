# EPOS2 50/5 emulator: pretends to be a maxon EPOS2 on a CAN bus

import select
import sys
import time

import can

from drive_state import decode_controlword, STATE_STATUSWORDS, TRANSITIONS
from motor import counts_to_rpm, Motor, rpm_to_counts
from object_dictionary import Entry, OBJECTS

# Name of the Linux CAN interface to use (vcan0 is virtual, can0 is real)
CAN_CHANNEL = "vcan0"

# CANopen node ID of this controller (the real unit sets it in hardware)
NODE_ID = 1
# CAN IDs for SDO traffic: base ID plus node ID (Communication Guide 6.3.3.2)
SDO_REQUEST_ID = 0x600 + NODE_ID
SDO_RESPONSE_ID = 0x580 + NODE_ID

# Every SDO frame carries exactly 8 data bytes
SDO_FRAME_SIZE_BYTES = 8
# Host command bytes for a write that fits in one frame, and the value size each
# one announces (Application Notes 10.4.1, expedited transfer, ccs = 1)
SDO_WRITE_SIZE_BYTES = {0x2F: 1, 0x2B: 2, 0x23: 4}
# Reply command bytes (Application Notes 10.4.1): write accepted, and write refused
SDO_WRITE_OK = 0x60
SDO_ABORT = 0x80
# Host command byte for a read, which needs no size (Application Notes 10.4.1)
SDO_READ_REQUEST = 0x40
# Reply command byte for a read, chosen by the size of the value being returned
# (Application Notes 10.4.1, expedited transfer, scs = 2)
SDO_READ_REPLY = {1: 0x4F, 2: 0x4B, 4: 0x43}

# Abort codes, sent back when a request is refused
# Communication Guide 8.1, page 8-47
ABORT_UNKNOWN_COMMAND = 0x05040001
ABORT_READ_ONLY = 0x06010002
ABORT_NO_OBJECT = 0x06020000
ABORT_LENGTH_MISMATCH = 0x06070010
ABORT_VALUE_RANGE = 0x06090030

# Addresses of the registers that drive the state machine and the motor
CONTROLWORD_ADDRESS = (0x6040, 0x00)
STATUSWORD_ADDRESS = (0x6041, 0x00)
MODE_ADDRESS = (0x6060, 0x00)
MODE_DISPLAY_ADDRESS = (0x6061, 0x00)
POSITION_ACTUAL_ADDRESS = (0x6064, 0x00)
VELOCITY_ACTUAL_ADDRESS = (0x606C, 0x00)
TARGET_POSITION_ADDRESS = (0x607A, 0x00)
PROFILE_VELOCITY_ADDRESS = (0x6081, 0x00)
PROFILE_ACCELERATION_ADDRESS = (0x6083, 0x00)
PROFILE_DECELERATION_ADDRESS = (0x6084, 0x00)
QUICKSTOP_DECELERATION_ADDRESS = (0x6085, 0x00)

# The only operating mode that moves the motor: Profile Position Mode
PROFILE_POSITION_MODE = 1
# Controlword bits for Profile Position Mode, from Table 5-16, page 5-38
NEW_SETPOINT_BIT = 1 << 4
CHANGE_IMMEDIATELY_BIT = 1 << 5
RELATIVE_BIT = 1 << 6
HALT_BIT = 1 << 8
# Statusword bits for Profile Position Mode, from Table 5-19, page 5-39
TARGET_REACHED_BIT = 1 << 10
SETPOINT_ACK_BIT = 1 << 12

# Seconds between motor updates, which is how often select must wake up
MOTOR_UPDATE_SECONDS = 0.01

# Current drive state, matching the Statusword's starting value
drive_state = "Switch On Disabled"
# The emulated axle
motor = Motor()
# True from accepting a setpoint until the host clears its new setpoint bit
setpoint_acknowledged = False


# Explanation of the emulator's output, printed by --help
OUTPUT_HELP = """\
Reading a register line:
  0x6041 sub 0x00 RO Statusword = 0x0140

  0x...       a number in hexadecimal (base 16)
  0x6041      index: the register's address
  sub 0x00    subindex: selects a part of a register, and 0x00 is the first
  RO          access: RO is read-only for the host, RW is read/write
  Statusword  the register's name in the firmware spec
  0x0140      the current value, with two hex digits per byte

Extra values on some lines:
  (-500)      the value in decimal, shown for signed registers
  0b0000_...  the value in binary, shown by reg -b, with bits grouped in fours

Example: 0x0140 in binary is 0b0000_0001_0100_0000. Bits count from 0 on the
right, so bits 6 and 8 are set. See the Statusword states in README.txt.

Reading the CAN log:
  RX id=<CAN ID> data=<bytes>

  RX          a frame was received
  id=         the frame's CAN ID, which says what kind of frame it is
  data=       the frame's data bytes in hex, with values sent low byte first

Every frame on the bus is logged. Only a request to this device gets a reply:
requests use CAN ID 0x600 + node ID, and replies use 0x580 + node ID. The lines
after a request say what the emulator did with it. See SDO frames in README.txt
for the meaning of each byte."""


# Print one register's address, access, name and value in hex, and optionally binary
def print_register(index: int, subindex: int, entry: Entry, show_binary: bool = False) -> None:
    access = "RW" if entry.writable else "RO"
    # Pad the hex value to its full width: two digits per byte
    hex_digits = entry.size_bytes * 2
    raw = entry.to_bus()
    binary = ""
    if show_binary:
        # Pad the binary value to its full width: eight bits per byte, plus one
        # underscore between each group of four bits
        binary_bits = entry.size_bytes * 8
        binary_width = binary_bits + binary_bits // 4 - 1
        binary = f" 0b{raw:0{binary_width}_b}"
    # Show signed registers in decimal too, since their hex is hard to read
    decimal = f" ({entry.value})" if entry.signed else ""
    print(f"  0x{index:04X} sub 0x{subindex:02X} {access} {entry.name} = "
          f"0x{raw:0{hex_digits}X}{binary}{decimal}")


# Print every register and its current value, as a debugging view of the device
def print_registers() -> None:
    print("Registers:")
    for (index, subindex), entry in OBJECTS.items():
        print_register(index, subindex, entry)


# Print the prompt that invites a typed command
def print_prompt() -> None:
    # Flush because the terminal only shows unfinished lines when told to
    print(">>> ", end="", flush=True)


# Run one command typed into the emulator's terminal
def handle_command(line: str) -> None:
    # Split the line into words, so extra spaces do not matter
    words = line.split()
    # An empty line does nothing
    if not words:
        return
    if words == ["regs"]:
        print_registers()
    elif words == ["help"]:
        print("Commands:")
        print("  regs           print all registers")
        print("  reg 0x6041     print one register (subindex 0x00)")
        print("  reg 0x6041 -b  also print its value in binary")
        print("  help           list the commands")
        print("  --help         explain the output")
    elif words == ["--help"]:
        print(OUTPUT_HELP)
    elif words[0] == "reg" and (len(words) == 2 or words[2:] == ["-b"]) and not words[1].startswith("-"):
        # Read the index as hex, with or without the 0x prefix
        try:
            index = int(words[1], 16)
        except ValueError:
            print(f"Not a hex index: {words[1]}")
            return
        entry = OBJECTS.get((index, 0x00))
        if entry is None:
            print(f"No register at index 0x{index:04X} sub 0x00")
            return
        print_register(index, 0x00, entry, show_binary=len(words) == 3)
    else:
        print(f"Unknown command: {line.strip()} (type help for a list)")


# Pull the fields out of an 8-byte SDO write request, or return None if it is not one
def decode_sdo_write(data: bytes) -> tuple[int, int, int, int] | None:
    # Byte 0 is the command, which also tells us the size of the value
    command = data[0]
    if command not in SDO_WRITE_SIZE_BYTES:
        return None
    size_bytes = SDO_WRITE_SIZE_BYTES[command]
    # Bytes 1-2 are the index, low byte first
    index = int.from_bytes(data[1:3], "little")
    # Byte 3 is the subindex
    subindex = data[3]
    # Bytes 4-7 hold the value, low byte first, and any spare bytes are unused
    value = int.from_bytes(data[4:4 + size_bytes], "little")
    return index, subindex, value, size_bytes


# Set the Statusword from the drive state, plus the motion bits while power is on
def update_statusword() -> None:
    statusword = STATE_STATUSWORDS[drive_state]
    if drive_state in ("Operation Enable", "Quickstop Active"):
        halted = OBJECTS[CONTROLWORD_ADDRESS].value & HALT_BIT
        # Reached means stopped, either with no move left or held by the Halt bit
        if motor.velocity == 0 and (motor.goal is None or halted):
            statusword |= TARGET_REACHED_BIT
        if setpoint_acknowledged:
            statusword |= SETPOINT_ACK_BIT
    OBJECTS[STATUSWORD_ADDRESS].value = statusword


# Show the motor's position and velocity in the registers
def publish_motor_values() -> None:
    OBJECTS[POSITION_ACTUAL_ADDRESS].value = round(motor.position)
    OBJECTS[VELOCITY_ACTUAL_ADDRESS].value = round(counts_to_rpm(motor.velocity))


# Act on the new setpoint bit: on a rising edge, the motor takes the Target Position
def handle_setpoint_bit(previous: int, controlword: int) -> None:
    global setpoint_acknowledged
    # Clearing the bit tells the host that another setpoint can be sent
    if not controlword & NEW_SETPOINT_BIT:
        setpoint_acknowledged = False
        return
    if previous & NEW_SETPOINT_BIT:
        return
    # A setpoint only means something while the drive runs in Profile Position Mode
    if drive_state != "Operation Enable" or OBJECTS[MODE_ADDRESS].value != PROFILE_POSITION_MODE:
        print("New setpoint ignored: needs Operation Enable and Profile Position Mode")
        return
    target = OBJECTS[TARGET_POSITION_ADDRESS].value
    relative = bool(controlword & RELATIVE_BIT)
    immediately = bool(controlword & CHANGE_IMMEDIATELY_BIT)
    motor.set_goal(target, relative, immediately)
    setpoint_acknowledged = True
    print(f"New setpoint: target={target} relative={relative} immediately={immediately}")


# Act on a stored Controlword write: change state and start or stop the motor
def update_drive_state(previous: int, controlword: int) -> None:
    global drive_state, setpoint_acknowledged
    command = decode_controlword(previous, controlword) or "no command"
    new_state = TRANSITIONS.get((drive_state, command))
    # A command that does not apply in this state leaves the drive where it is
    if new_state is None:
        print(f"Controlword 0x{controlword:04X}: {command}, staying in {drive_state}")
    else:
        print(f"Controlword 0x{controlword:04X}: {command}, {drive_state} -> {new_state}")
        drive_state = new_state
        # Quickstop ramps the motor down, and any other way out of running cuts power
        if new_state == "Quickstop Active":
            motor.cancel_moves()
        elif new_state != "Operation Enable":
            motor.cut_power()
        if new_state != "Operation Enable":
            setpoint_acknowledged = False
    handle_setpoint_bit(previous, controlword)
    publish_motor_values()
    update_statusword()


# Advance the motor by dt seconds and publish the result in the registers
# Returns True if the last move just finished
def update_motor(dt: float) -> bool:
    halt = bool(OBJECTS[CONTROLWORD_ADDRESS].value & HALT_BIT)
    # Quickstop has its own deceleration, and every other ramp uses the profile's
    deceleration_address = (QUICKSTOP_DECELERATION_ADDRESS if drive_state == "Quickstop Active"
                            else PROFILE_DECELERATION_ADDRESS)
    finished = motor.step(dt,
                          rpm_to_counts(OBJECTS[PROFILE_VELOCITY_ADDRESS].value),
                          rpm_to_counts(OBJECTS[PROFILE_ACCELERATION_ADDRESS].value),
                          rpm_to_counts(OBJECTS[deceleration_address].value),
                          halt)
    publish_motor_values()
    update_statusword()
    return finished


# Store a written value in OBJECTS, or return the abort code if it is refused
def apply_sdo_write(index: int, subindex: int, value: int, size_bytes: int) -> int | None:
    # The address must exist in the object dictionary
    entry = OBJECTS.get((index, subindex))
    if entry is None:
        return ABORT_NO_OBJECT
    # The spec must mark the object "RW"
    if not entry.writable:
        return ABORT_READ_ONLY
    # The size must match the object's data type exactly
    if size_bytes != entry.size_bytes:
        return ABORT_LENGTH_MISMATCH
    new_value = entry.from_bus(value)
    # The spec may limit the object to certain values
    if entry.allowed is not None and new_value not in entry.allowed:
        return ABORT_VALUE_RANGE
    # Every check passed, so the write takes effect
    previous = entry.value
    entry.value = new_value
    if (index, subindex) == CONTROLWORD_ADDRESS:
        update_drive_state(previous, new_value)
    elif (index, subindex) == MODE_ADDRESS:
        OBJECTS[MODE_DISPLAY_ADDRESS].value = new_value
    return None


# Send one SDO reply: command, the address it answers, and 4 payload bytes
def send_sdo_reply(bus: can.BusABC, command: int, index: int, subindex: int, payload: int) -> None:
    # Lay out the same fields as the request, low byte first
    data = bytes([command]) + index.to_bytes(2, "little") + bytes([subindex]) + payload.to_bytes(4, "little")
    reply = can.Message(arbitration_id=SDO_RESPONSE_ID, data=data, is_extended_id=False)
    try:
        bus.send(reply)
    except can.CanError as error:
        # The host will time out, so make the failure visible here
        print(f"SDO reply failed to send: {error}")


# Handle one SDO read: look up the register and reply with its value
def handle_sdo_read(bus: can.BusABC, data: bytes) -> None:
    # Bytes 1-2 are the index, low byte first, and byte 3 is the subindex
    index = int.from_bytes(data[1:3], "little")
    subindex = data[3]
    print(f"SDO read index=0x{index:04X} sub=0x{subindex:02X}")
    entry = OBJECTS.get((index, subindex))
    # A missing address gets an abort, the same as a write to it
    if entry is None:
        print(f"SDO read refused, sending abort 0x{ABORT_NO_OBJECT:08X} on 0x{SDO_RESPONSE_ID:03X}")
        send_sdo_reply(bus, SDO_ABORT, index, subindex, ABORT_NO_OBJECT)
        return
    # The reply command tells the host how many value bytes to use
    print(f"SDO read ok, sending 0x{entry.to_bus():X} on 0x{SDO_RESPONSE_ID:03X}")
    send_sdo_reply(bus, SDO_READ_REPLY[entry.size_bytes], index, subindex, entry.to_bus())


# Answer a request the emulator cannot understand with an abort
def refuse_unsupported_request(bus: can.BusABC, data: bytes, reason: str) -> None:
    # A short frame may not hold the address, so missing bytes count as zero
    index = int.from_bytes(data[1:3], "little")
    subindex = data[3] if len(data) > 3 else 0
    print(f"SDO unsupported: {reason}, sending abort 0x{ABORT_UNKNOWN_COMMAND:08X} on 0x{SDO_RESPONSE_ID:03X}")
    send_sdo_reply(bus, SDO_ABORT, index, subindex, ABORT_UNKNOWN_COMMAND)


# Handle one SDO request: read or write a register, and reply to the host
def handle_sdo_request(bus: can.BusABC, data: bytes) -> None:
    # Every SDO frame has 8 data bytes, so anything else is not a valid request
    if len(data) != SDO_FRAME_SIZE_BYTES:
        refuse_unsupported_request(bus, data, f"expected 8 data bytes, got {len(data)}")
        return
    # The host is cancelling a transfer, and an abort must never be answered
    if data[0] == SDO_ABORT:
        print("SDO abort received from host, no reply")
        return
    # Reads are identified first, since they have no size or value to decode
    if data[0] == SDO_READ_REQUEST:
        handle_sdo_read(bus, data)
        return
    request = decode_sdo_write(data)
    if request is None:
        # Includes multi-frame transfers, which are not built yet
        refuse_unsupported_request(bus, data, f"command 0x{data[0]:02X}")
        return
    index, subindex, value, size_bytes = request
    print(f"SDO write index=0x{index:04X} sub=0x{subindex:02X} value=0x{value:X} size_bytes={size_bytes}")
    # Apply the write, then tell the host whether it worked
    abort_code = apply_sdo_write(index, subindex, value, size_bytes)
    if abort_code is None:
        print(f"SDO write stored, sending ok on 0x{SDO_RESPONSE_ID:03X}")
        send_sdo_reply(bus, SDO_WRITE_OK, index, subindex, 0)
        # Show the new register values after every change
        print_registers()
    else:
        print(f"SDO write refused, sending abort 0x{abort_code:08X} on 0x{SDO_RESPONSE_ID:03X}")
        send_sdo_reply(bus, SDO_ABORT, index, subindex, abort_code)


# Run the emulator when this file is started from the command line
def main() -> None:
    # Explain the output and stop, without needing a CAN interface
    if sys.argv[1:] == ["--help"]:
        print(OUTPUT_HELP)
        return
    # Announce startup so we can see the program actually ran
    print(f"EPOS2 emulator starting on {CAN_CHANNEL}")
    # Show the startup register values
    print_registers()

    # Open the CAN interface through Linux SocketCAN
    # The "with" block closes the interface automatically when we exit
    with can.Bus(interface="socketcan", channel=CAN_CHANNEL) as bus:
        try:
            # The two inputs to watch: CAN frames and lines typed in the terminal
            inputs = [bus, sys.stdin]
            print_prompt()
            last_update = time.monotonic()
            # Handle whichever input is ready, and keep the motor moving, forever
            while True:
                # Wait for an input, but wake up in time for the next motor update
                ready, _, _ = select.select(inputs, [], [], MOTOR_UPDATE_SECONDS)
                now = time.monotonic()
                if now - last_update >= MOTOR_UPDATE_SECONDS:
                    if update_motor(now - last_update):
                        # Finish the prompt's line so this log starts on its own
                        print()
                        print(f"Target reached at {OBJECTS[POSITION_ACTUAL_ADDRESS].value}")
                        print_prompt()
                    last_update = now
                # A wake-up just for the motor has nothing more to do
                if not ready:
                    continue
                if bus in ready:
                    # A frame is waiting, so this returns without blocking
                    frame = bus.recv(timeout=0)
                    # Nothing arrived after all, so go back to waiting
                    if frame is None:
                        continue
                    # Finish the prompt's line so the frame log starts on its own
                    print()
                    # Print the CAN ID and data bytes in hex, like candump
                    print(f"RX id=0x{frame.arbitration_id:03X} data={frame.data.hex(' ').upper()}")
                    # Only standard 11-bit data frames with our request ID are SDOs for us
                    if (frame.arbitration_id == SDO_REQUEST_ID and not frame.is_extended_id
                            and not frame.is_remote_frame):
                        handle_sdo_request(bus, bytes(frame.data))
                if sys.stdin in ready:
                    line = sys.stdin.readline()
                    if line:
                        handle_command(line)
                    else:
                        # End of input: stop watching it, or select would keep
                        # reporting it as ready, and keep serving the bus
                        print("Terminal input closed, CAN handling continues")
                        inputs.remove(sys.stdin)
                # Show the prompt again after any output, if input is still open
                if sys.stdin in inputs:
                    print_prompt()
        except KeyboardInterrupt:
            # Stop cleanly when the user presses Ctrl+C
            print("\nEPOS2 emulator stopped")


# Only call main() when run directly, not when imported by another file
if __name__ == "__main__":
    main()
