# EPOS2 50/5 emulator: pretends to be a maxon EPOS2 on a CAN bus

import can

# Name of the Linux CAN interface to use (vcan0 is virtual, can0 is real)
CAN_CHANNEL = "vcan0"


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
        except KeyboardInterrupt:
            # Stop cleanly when the user presses Ctrl+C
            print("\nEPOS2 emulator stopped")


# Only call main() when run directly, not when imported by another file
if __name__ == "__main__":
    main()
