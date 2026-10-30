# EPOS2 50/5 emulator: pretends to be a maxon EPOS2 on a CAN bus

import select
import sys

import can

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

# Abort codes, sent back when a write is refused
# Communication Guide 8.1, page 8-47
ABORT_READ_ONLY = 0x06010002
ABORT_NO_OBJECT = 0x06020000
ABORT_LENGTH_MISMATCH = 0x06070010


# Print one register's address, access, name and value in hex and binary
def print_register(index: int, subindex: int, entry: Entry) -> None:
    access = "RW" if entry.writable else "RO"
    # Pad the hex value to its full width: two digits per byte
    hex_digits = entry.size_bytes * 2
    # Pad the binary value to its full width: eight bits per byte, plus one
    # underscore between each group of four bits
    binary_bits = entry.size_bytes * 8
    binary_width = binary_bits + binary_bits // 4 - 1
    print(f"  0x{index:04X} sub 0x{subindex:02X} {access} {entry.name} = "
          f"0x{entry.value:0{hex_digits}X} 0b{entry.value:0{binary_width}_b}")


# Print every register and its current value, as a debugging view of the device
def print_registers() -> None:
    print("Registers:")
    for (index, subindex), entry in OBJECTS.items():
        print_register(index, subindex, entry)


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
        print("  regs        print all registers")
        print("  reg 0x6041  print one register (subindex 0x00)")
        print("  help        list the commands")
    elif words[0] == "reg" and len(words) == 2:
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
        print_register(index, 0x00, entry)
    else:
        print(f"Unknown command: {line.strip()} (type help for a list)")


# Pull the fields out of an SDO write request, or return None if it is not one
def decode_sdo_write(data: bytes) -> tuple[int, int, int, int] | None:
    # Refuse frames too short to hold all the fields
    if len(data) != SDO_FRAME_SIZE_BYTES:
        print(f"SDO unsupported: expected 8 data bytes, got {len(data)}")
        return None
    # Byte 0 is the command, which also tells us the size of the value
    command = data[0]
    if command not in SDO_WRITE_SIZE_BYTES:
        # Reads and multi-frame transfers are not built yet
        print(f"SDO unsupported: command 0x{command:02X}")
        return None
    size_bytes = SDO_WRITE_SIZE_BYTES[command]
    # Bytes 1-2 are the index, low byte first
    index = int.from_bytes(data[1:3], "little")
    # Byte 3 is the subindex
    subindex = data[3]
    # Bytes 4-7 hold the value, low byte first, and any spare bytes are unused
    value = int.from_bytes(data[4:4 + size_bytes], "little")
    return index, subindex, value, size_bytes


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
    # Every check passed, so the write takes effect
    entry.value = value
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
    print(f"SDO read ok, sending 0x{entry.value:X} on 0x{SDO_RESPONSE_ID:03X}")
    send_sdo_reply(bus, SDO_READ_REPLY[entry.size_bytes], index, subindex, entry.value)


# Handle one SDO request: read or write a register, and reply to the host
def handle_sdo_request(bus: can.BusABC, data: bytes) -> None:
    # Reads are identified first, since they have no size or value to decode
    if len(data) == SDO_FRAME_SIZE_BYTES and data[0] == SDO_READ_REQUEST:
        handle_sdo_read(bus, data)
        return
    # Unsupported requests were already reported, and get no reply yet
    request = decode_sdo_write(data)
    if request is None:
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
            # Handle whichever input is ready, forever
            while True:
                # Block until at least one input has something to read
                ready, _, _ = select.select(inputs, [], [])
                if bus in ready:
                    # A frame is waiting, so this returns without blocking
                    frame = bus.recv(timeout=0)
                    if frame is not None:
                        # Print the CAN ID and data bytes in hex, like candump
                        print(f"RX id=0x{frame.arbitration_id:03X} data={frame.data.hex(' ').upper()}")
                        # Only standard 11-bit frames with our request ID are SDOs for us
                        if frame.arbitration_id == SDO_REQUEST_ID and not frame.is_extended_id:
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
        except KeyboardInterrupt:
            # Stop cleanly when the user presses Ctrl+C
            print("\nEPOS2 emulator stopped")


# Only call main() when run directly, not when imported by another file
if __name__ == "__main__":
    main()
