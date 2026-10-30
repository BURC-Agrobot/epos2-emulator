EPOS2 emulator
==============

Emulates a maxon EPOS2 50/5 motor controller as a device on a CAN bus, so
host software can be tested without real hardware.

The host plays the role of a CPU. The controller's object dictionary is its
memory-mapped register space, and SDO messages are its bus transactions: a
command byte selects read or write, the index and subindex form the address,
and the remaining bytes carry the data. Host and device are separate computers
with no shared clock, so each access is an asynchronous request and reply.

So far the emulator prints each frame it receives and accepts SDO writes of 1,
2 or 4 bytes to the object dictionary. SDO reads and multi-frame transfers are
not built yet: it prints "SDO unsupported" and sends no reply.

Setup (once, Ubuntu)
--------------------
    sudo apt install python3-venv can-utils
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt

Virtual CAN bus (after every reboot or WSL restart)
---------------------------------------------------
    sudo modprobe vcan
    sudo ip link add dev vcan0 type vcan
    sudo ip link set up vcan0

Check it with two terminals:
    candump vcan0              (terminal 1: prints every frame)
    cansend vcan0 123#DEADBEEF (terminal 2: sends one frame)

Run
---
    .venv/bin/python emulator.py

Object dictionary
-----------------
The registers currently defined, from OBJECTS in object_dictionary.py. Update
this list whenever OBJECTS changes. Types and access come from the EPOS2
Firmware Specification, chapter 8.

Unlike a flat byte-addressed space, each address names a whole object with its
own width and access rights, so an access covers the full value.

Role is how the register behaves:
    Storage: holds a value and does nothing else.
    Status:  reports the device's state to the host.
    Control: a write from the host commands the device.
Sent is the type and width in a CAN frame, always low byte first.
Stored is how the code holds the value: always a Python int in Entry.value.
Starts at is the value held at startup.

0x1000 sub 0x00  Device Type (read-only)
    Role:      Storage
    Identifies the device. The low half, 0x0192 (402), means the CiA 402
    drive profile. The high half, 0x0002, means a servo drive. The value is
    constant.
    Sent:      UNSIGNED32, 4 bytes
    Stored:    Python int
    Starts at: 0x00020192

0x1001 sub 0x00  Error Register (read-only)
    Role:      Status
    Summarizes current errors, with one bit per error class. Zero means no
    errors.
    Sent:      UNSIGNED8, 1 byte
    Stored:    Python int
    Starts at: 0x00

0x6040 sub 0x00  Controlword (read/write)
    Role:      Control
    Carries host commands to the drive state machine, with bits for device
    control and operating mode supervision. The emulator only stores this
    value so far and does not act on it.
    Sent:      UNSIGNED16, 2 bytes
    Stored:    Python int
    Starts at: 0x0000 (the spec gives no default, so no command)

0x6041 sub 0x00  Statusword (read-only)
    Role:      Status
    Reports the drive's current state, with bits for the state machine and
    the operating mode. The emulator does not update this value yet.
    Sent:      UNSIGNED16, 2 bytes
    Stored:    Python int
    Starts at: 0x0140 (the spec gives no default: this is the "Switch On
               Disabled" state with offset current measured, bits 8 and 6)

Updating a register
-------------------
With the emulator running, send an SDO write to node 1 on CAN ID 601, and watch
the reply with candump vcan0 in another terminal:
    cansend vcan0 601#2B40600006000000

The 8 data bytes, with multi-byte fields low byte first:
    Byte 0:    command, set by the register's size (2F = 1 byte, 2B = 2 bytes,
               23 = 4 bytes)
    Bytes 1-2: index (40 60 = 0x6040)
    Byte 3:    subindex (00)
    Bytes 4-7: value, with unused bytes set to 00 (06 00 00 00 = 0x0006)

The reply arrives on CAN ID 581:
    60 means the value was stored.
    80 means the write was refused, and bytes 4-7 hold the abort code:
        0x06020000  the index and subindex do not exist
        0x06010002  the register is read-only
        0x06070010  the size does not match the register

Only Controlword (0x6040) is writable today. Written values are lost when the
emulator stops, and writing Controlword does not change the Statusword yet.
There is no SDO read yet, so the emulator's printed output and the reply are
the only confirmation of a write.
