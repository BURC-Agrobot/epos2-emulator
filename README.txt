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

SDO read and write
------------------
An SDO is a bus transaction carried in two CAN frames: the host sends one
request, and the device sends one reply. The host is the initiator, and the
device owning the object dictionary only ever responds. Unlike a CPU bus access,
nothing happens on a shared clock: the host must wait for the reply and treat a
missing one as a timeout. Frame formats below come from the EPOS2 Application
Notes, section 10.4. All frames here are for node 1.

Status in this emulator:
    Write: built for 2F, 2B and 23, as shown below.
    Read:  not built. The read frames show what a real EPOS2 sends.

CAN ID: the channel
    601  request, host to device (0x600 + node ID)
    581  reply, device to host (0x580 + node ID)

Data bytes: always 8, with multi-byte fields low byte first
    Byte 0     command: the operation, and the value size if it has one
    Bytes 1-2  index, the first part of the address
    Byte 3     subindex, the second part of the address
    Bytes 4-7  value or abort code, with unused bytes set to 00

Command byte: bits 7-5 select the operation, and bits 3-2 count the unused
value bytes
    Command  Bits       Meaning
    2F       0010 1111  write request, 1 byte (3 unused)
    2B       0010 1011  write request, 2 bytes (2 unused)
    23       0010 0011  write request, 4 bytes (0 unused)
    60       0110 0000  write reply: value stored
    40       0100 0000  read request (the size comes from the object)
    4F       0100 1111  read reply, 1 byte (3 unused)
    4B       0100 1011  read reply, 2 bytes (2 unused)
    43       0100 0011  read reply, 4 bytes (0 unused)
    80       1000 0000  abort reply: bytes 4-7 hold the abort code

To send a request, join its ID and bytes with no spaces, as in the first write:
    cansend vcan0 601#2B40600006000000
Run candump vcan0 in another terminal to see the reply.

Write: store Controlword = 0x0006 at 0x6040
    Request  601  2B 40 60 00 06 00 00 00
    Reply    581  60 40 60 00 00 00 00 00

    2B           write 2 bytes
    40 60        index 0x6040, low byte first
    00           subindex 0x00
    06 00        value 0x0006, low byte first
    00 00        unused
    60           the reply: stored
    40 60 00     the reply repeats the address, so the host can match it
    00 00 00 00  a successful write returns no data

Write: address does not exist
    Request  601  2B 00 20 00 01 00 00 00
    Reply    581  80 00 20 00 00 00 02 06
    Abort code 0x06020000, sent low byte first as 00 00 02 06.

Write: register is read-only (Statusword)
    Request  601  2B 41 60 00 01 00 00 00
    Reply    581  80 41 60 00 02 00 01 06
    Abort code 0x06010002.

Write: size does not match the register (4 bytes to 2-byte Controlword)
    Request  601  23 40 60 00 01 00 00 00
    Reply    581  80 40 60 00 10 00 07 06
    Abort code 0x06070010.

Write: 1 byte to the read-only Error Register
    Request  601  2F 01 10 00 01 00 00 00
    Reply    581  80 01 10 00 02 00 01 06
    Abort code 0x06010002. The read-only check runs before the size check. No
    1-byte or 4-byte register is writable yet, so 2F and 23 cannot store a
    value today.

How the emulator handles a write, in emulator.py:
    1. main() matches CAN ID 601 and calls handle_sdo_request.
    2. decode_sdo_write checks for 8 bytes and a known command, then extracts
       the index, subindex, value and size.
    3. apply_sdo_write checks that the object exists, is writable and has the
       right size, in that order, then stores the value in OBJECTS.
    4. send_sdo_reply sends 60, or 80 with the abort code.

Read: Statusword at 0x6041 (not built, values are the emulator's startup values)
    Request  601  40 41 60 00 00 00 00 00
    Reply    581  4B 41 60 00 40 01 00 00

    40           read
    41 60 00     address 0x6041, subindex 0x00
    00 00 00 00  a read request carries no data
    4B           the reply: 2 bytes follow
    40 01        value 0x0140, low byte first
    00 00        unused

Read: other reply sizes (not built)
    1 byte, Error Register at 0x1001
    Request  601  40 01 10 00 00 00 00 00
    Reply    581  4F 01 10 00 00 00 00 00

    4 bytes, Device Type at 0x1000, value 0x00020192
    Request  601  40 00 10 00 00 00 00 00
    Reply    581  43 00 10 00 92 01 02 00

Today the emulator answers every read with no reply. It prints "SDO unsupported",
so a host waiting for the value will time out. A real EPOS2 answers a read of a
missing object with an 80 abort.

Other limits:
    Only Controlword (0x6040) is writable today.
    Written values are lost when the emulator stops.
    Writing Controlword does not change the Statusword yet.
    Command 22 (a write with no stated size) is valid on a real EPOS2, but the
    emulator treats it as unsupported.
    A real EPOS2 can use abort code 0x06090011 for a wrong subindex. The
    emulator always sends 0x06020000 for a missing address.
