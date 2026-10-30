# EPOS2 50/5 emulator: pretends to be a maxon EPOS2 on a CAN bus

import can

from object_dictionary import OBJECTS

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

# Abort codes, sent back when a write is refused
# Communication Guide 8.1, page 8-47
ABORT_READ_ONLY = 0x06010002
ABORT_NO_OBJECT = 0x06020000
ABORT_LENGTH_MISMATCH = 0x06070010


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


# Handle one SDO request: decode it, apply it, and reply to the host
def handle_sdo_request(bus: can.BusABC, data: bytes) -> None:
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
    else:
        print(f"SDO write refused, sending abort 0x{abort_code:08X} on 0x{SDO_RESPONSE_ID:03X}")
        send_sdo_reply(bus, SDO_ABORT, index, subindex, abort_code)


# Run the emulator when this file is started from the command line
def main() -> None:
    # Announce startup so we can see the program actually ran
    print(f"EPOS2 emulator starting on {CAN_CHANNEL}")

    # Open the CAN interface through Linux SocketCAN
    # The "with" block closes the interface automatically when we exit
    with can.Bus(interface="socketcan", channel=CAN_CHANNEL) as bus:
        try:
            # Wait for frames forever, one at a time
            while True:
                # Block until the next frame arrives on the bus
                frame = bus.recv()
                # Print the CAN ID and data bytes in hex, like candump
                print(f"RX id=0x{frame.arbitration_id:03X} data={frame.data.hex(' ').upper()}")
                # Only standard 11-bit frames with our request ID are SDOs for us
                if frame.arbitration_id == SDO_REQUEST_ID and not frame.is_extended_id:
                    handle_sdo_request(bus, bytes(frame.data))
        except KeyboardInterrupt:
            # Stop cleanly when the user presses Ctrl+C
            print("\nEPOS2 emulator stopped")


# Only call main() when run directly, not when imported by another file
if __name__ == "__main__":
    main()
