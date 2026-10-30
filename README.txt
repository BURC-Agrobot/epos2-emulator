EPOS2 emulator
==============

Emulates a maxon EPOS2 50/5 motor controller as a device on a CAN bus, so
host software can be tested without real hardware.

The host plays the role of a CPU. The controller's object dictionary is its
memory-mapped register space, and SDO messages are its bus transactions: a
command byte selects read or write, the index and subindex form the address,
and the remaining bytes carry the data. Host and device are separate computers
with no shared clock, so each access is an asynchronous request and reply.

So far the emulator prints each frame it receives and answers single-frame SDO
reads and writes of the object dictionary. Multi-frame transfers are not built
yet: it prints "SDO unsupported" and sends an abort reply. Controlword writes move
the drive through its states, and the Statusword reports the result. In Profile
Position Mode, a simple motor model moves to the positions the host commands.

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

Seeing the registers
--------------------
The emulator prints every register and its current value at startup and after
each stored write. You can also type these commands at its >>> prompt while it
runs:
    regs        print all registers
    reg 0x6041  print one register (subindex 0x00, with or without the 0x)
    help        list the commands

These commands only look at the emulator's registers and send nothing on the
CAN bus. Frame logs may print between your commands, since everything shares
one terminal. If a frame arrives while you are typing, your text stays in the
terminal's buffer and still runs when you press Enter, but it is not redrawn
after the new prompt. To get a register's value over CAN, use an SDO read.

Each line shows the index, subindex, access (RO or RW), name and current value
in hex and binary, padded to the register's full width, with binary bits grouped
in fours:
    0x6041 sub 0x00 RO Statusword = 0x0140 0b0000_0001_0100_0000

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
    control and operating mode supervision. The emulator acts on the device
    control bits (see Drive states) and the Profile Position Mode bits (see
    Motion), and ignores the rest.
    Sent:      UNSIGNED16, 2 bytes
    Stored:    Python int
    Starts at: 0x0000 (the spec gives no default, so no command)

0x6041 sub 0x00  Statusword (read-only)
    Role:      Status
    Reports the drive's current state, with bits for the state machine and
    the operating mode. The emulator sets it to the value for the current
    drive state (see Drive states), plus the motion bits (see Motion).
    Sent:      UNSIGNED16, 2 bytes
    Stored:    Python int
    Starts at: 0x0140 (the spec gives no default: this is the "Switch On
               Disabled" state with offset current measured, bits 8 and 6)

0x6060 sub 0x00  Modes of Operation (read/write)
    Role:      Control
    Selects the operating mode. Any mode in the spec's table is accepted, but
    only 1 (Profile Position Mode) moves the motor.
    Sent:      INTEGER8, 1 byte
    Stored:    Python int
    Starts at: 1

0x6061 sub 0x00  Modes of Operation Display (read-only)
    Role:      Status
    Reports the operating mode in use. It follows 0x6060 at once.
    Sent:      INTEGER8, 1 byte
    Stored:    Python int
    Starts at: 1

0x6064 sub 0x00  Position Actual Value (read-only)
    Role:      Status
    The motor's position in quadcounts.
    Sent:      INTEGER32, 4 bytes
    Stored:    Python int
    Starts at: 0 (the spec gives no default)

0x606C sub 0x00  Velocity Actual Value (read-only)
    Role:      Status
    The motor's velocity in rpm, negative when moving backwards.
    Sent:      INTEGER32, 4 bytes
    Stored:    Python int
    Starts at: 0 (the spec gives no default)

0x607A sub 0x00  Target Position (read/write)
    Role:      Storage
    The position to move to, in quadcounts. Writing it does nothing until the
    host sets the new setpoint bit in Controlword.
    Sent:      INTEGER32, 4 bytes
    Stored:    Python int
    Starts at: 0

0x6081 sub 0x00  Profile Velocity (read/write)
    Role:      Storage
    The cruising speed of a move, in rpm. Must be above zero.
    Sent:      UNSIGNED32, 4 bytes
    Stored:    Python int
    Starts at: 1000

0x6083 sub 0x00  Profile Acceleration (read/write)
    Role:      Storage
    The ramp up to speed, in rpm/s. Must be above zero.
    Sent:      UNSIGNED32, 4 bytes
    Stored:    Python int
    Starts at: 10000

0x6084 sub 0x00  Profile Deceleration (read/write)
    Role:      Storage
    The ramp down to a stop, in rpm/s. Must be above zero.
    Sent:      UNSIGNED32, 4 bytes
    Stored:    Python int
    Starts at: 10000

0x6085 sub 0x00  Quickstop Deceleration (read/write)
    Role:      Storage
    The ramp down during a Quickstop, in rpm/s. Must be above zero.
    Sent:      UNSIGNED32, 4 bytes
    Stored:    Python int
    Starts at: 10000

Sending commands
----------------
An SDO is a bus transaction carried in two CAN frames: the host sends one
request, and the device sends one reply. Unlike a CPU bus access, nothing
happens on a shared clock: the host must wait for the reply and treat a missing
one as a timeout. Frame formats come from the EPOS2 Application Notes, section
10.4. All frames here are for node 1.

Every command has the same syntax, written as hex with no spaces:
    cansend vcan0 601#<command><index low><index high><subindex><value>

    601          CAN ID of a request to node 1 (0x600 + node ID)
    command      1 byte: the operation and size, from the command bytes below
    index low    1 byte: low byte of the index
    index high   1 byte: high byte of the index
    subindex     1 byte
    value        4 bytes, low byte first, with unused bytes set to 00
                 A read sends 00 00 00 00.

Example: write 0x0006 to Controlword (index 0x6040, subindex 0x00)
    cansend vcan0 601#2B40600006000000

    2B           write 2 bytes
    40 60        index 0x6040, low byte first
    00           subindex 0x00
    06 00        value 0x0006, low byte first
    00 00        unused

Run candump vcan0 in another terminal to see the reply. It arrives on CAN ID
581 (0x580 + node ID), with the same address in bytes 1-3 so the host can match
it to the request.

Command bytes: bits 7-5 select the operation, and bits 3-2 count the unused
value bytes
    Byte  Bits       Sent by  Meaning
    40    0100 0000  host     read request (the size comes from the object)
    2F    0010 1111  host     write request, 1 byte (3 unused)
    2B    0010 1011  host     write request, 2 bytes (2 unused)
    23    0010 0011  host     write request, 4 bytes (0 unused)
    4F    0100 1111  device   read reply, 1 byte (3 unused)
    4B    0100 1011  device   read reply, 2 bytes (2 unused)
    43    0100 0011  device   read reply, 4 bytes (0 unused)
    60    0110 0000  device   write reply: value stored
    80    1000 0000  either   abort: bytes 4-7 hold the abort code

Abort codes, sent low byte first in bytes 4-7 of an 80 reply:
    Code        Bytes        Meaning
    0x05040001  01 00 04 05  the command is unknown, or the frame is not 8 bytes
    0x06020000  00 00 02 06  the index and subindex do not exist
    0x06010002  02 00 01 06  the register is read-only
    0x06070010  10 00 07 06  the size does not match the register
    0x06090030  30 00 09 06  the value is outside the register's allowed range

Command table: run these in order on a freshly started emulator, since the
write changes the Controlword value read in the last row. It also moves the drive
to Ready to Switch On, so the Statusword reads 0x0121 afterwards.

    Action                  Command                             Reply on 581             Result
    Read Device Type        cansend vcan0 601#4000100000000000  43 00 10 00 92 01 02 00  0x00020192
    Read Error Register     cansend vcan0 601#4001100000000000  4F 01 10 00 00 00 00 00  0x00
    Read Controlword        cansend vcan0 601#4040600000000000  4B 40 60 00 00 00 00 00  0x0000
    Read Statusword         cansend vcan0 601#4041600000000000  4B 41 60 00 40 01 00 00  0x0140
    Read missing address    cansend vcan0 601#4000200000000000  80 00 20 00 00 00 02 06  abort: no object
    Write Controlword       cansend vcan0 601#2B40600006000000  60 40 60 00 00 00 00 00  stored 0x0006
    Write missing address   cansend vcan0 601#2B00200001000000  80 00 20 00 00 00 02 06  abort: no object
    Write Statusword        cansend vcan0 601#2B41600001000000  80 41 60 00 02 00 01 06  abort: read-only
    Write Controlword, 4 B  cansend vcan0 601#2340600001000000  80 40 60 00 10 00 07 06  abort: wrong size
    Write Error Register    cansend vcan0 601#2F01100001000000  80 01 10 00 02 00 01 06  abort: read-only
    Unknown command         cansend vcan0 601#2240600006000000  80 40 60 00 01 00 04 05  abort: unknown command
    Frame too short         cansend vcan0 601#404060            80 40 60 00 01 00 04 05  abort: unknown command
    Read Controlword again  cansend vcan0 601#4040600000000000  4B 40 60 00 06 00 00 00  0x0006

The read-only check runs before the size check, which is why the 1-byte write to
the Error Register gets a read-only abort.

How the emulator handles a request, in emulator.py:
    1. main() matches data frames on CAN ID 601 and calls handle_sdo_request.
    2. A frame without 8 data bytes is refused with abort 0x05040001.
    3. For a read, handle_sdo_read looks up the address in OBJECTS.
    4. For a write, decode_sdo_write checks for a known command, then extracts
       the index, subindex, value and size. An unknown command is refused with
       abort 0x05040001.
       apply_sdo_write then checks that the object exists, is writable, has the
       right size and allows the value, in that order, and stores the value in
       OBJECTS.
    5. send_sdo_reply sends the reply: 4F, 4B, 43 or 60 on success, or 80 with
       the abort code.

Every request on 601 gets exactly one reply, with two exceptions:
    A host abort (command 80) gets no reply, since an abort is never answered.
    A remote frame (RTR) is not a request, so it is logged and ignored.

Limits:
    A refused frame's reply repeats bytes 1-3 of the request as the address, and
    any bytes a short frame does not have are sent as 00.
    Written values are lost when the emulator stops.
    Command 22 (a write with no stated size) is valid on a real EPOS2, but the
    emulator refuses it as an unknown command. Multi-frame transfer commands get
    the same abort.
    The Communication Guide does not say how a real EPOS2 answers a frame without
    8 data bytes. Abort 0x05040001 is the emulator's choice.
    A real EPOS2 can use abort code 0x06090011 for a wrong subindex. The
    emulator always sends 0x06020000 for a missing address.

Drive states
------------
A real drive only runs after the host walks it through a fixed sequence of
states. The host requests each change through Controlword, and the drive reports
its state through Statusword.

STATE_STATUSWORDS in drive_state.py lists the 12 states and the Statusword value
for each, from the EPOS2 Firmware Specification, Table 3-4, page 3-15. The spec
marks some bits as "x", meaning they are not part of the state, and the values
here set them to 0.

The emulator starts in Switch On Disabled. After each stored Controlword write,
decode_controlword names the command, TRANSITIONS gives the next state, and the
Statusword is set to that state's value, plus the motion bits in Operation Enable
and Quickstop Active (see Motion). A command that does not apply in the
current state is still stored and acknowledged, but the state stays the same.

    State                            Statusword
    Start                             0x0000
    Not Ready to Switch On            0x0100
    Switch On Disabled                0x0140
    Ready to Switch On                0x0121
    Switched On                       0x0123
    Refresh                           0x4123
    Measure Init                      0x4133
    Operation Enable                  0x0137
    Quickstop Active                  0x0117
    Fault Reaction Active (disabled)  0x010F
    Fault Reaction Active (enabled)   0x011F
    Fault                             0x0108

Commands, from the low byte of the Controlword (Table 3-6, page 3-17), where x
means the bit is ignored:
    Command            Low byte   Example
    Shutdown           0xxx x110  0x0006
    Switch On          0xxx 0111  0x0007
    Enable Operation   0xxx 1111  0x000F
    Disable Operation  0xxx 0111  0x0007 (Switch On, sent in Operation Enable)
    Disable Voltage    0xxx xx0x  0x0000
    Quickstop          0xxx x01x  0x0002
    Fault Reset        bit 7 goes from 0 to 1  0x0080

Transitions (Table 3-5, page 3-16):
    From                Command            To
    Switch On Disabled  Shutdown           Ready to Switch On
    Ready to Switch On  Switch On          Switched On
    Ready to Switch On  Enable Operation   Operation Enable
    Ready to Switch On  Disable Voltage    Switch On Disabled
    Ready to Switch On  Quickstop          Switch On Disabled
    Switched On         Shutdown           Ready to Switch On
    Switched On         Enable Operation   Operation Enable
    Switched On         Disable Voltage    Switch On Disabled
    Switched On         Quickstop          Switch On Disabled
    Operation Enable    Disable Operation  Switched On
    Operation Enable    Shutdown           Ready to Switch On
    Operation Enable    Disable Voltage    Switch On Disabled
    Operation Enable    Quickstop          Quickstop Active
    Quickstop Active    Disable Voltage    Switch On Disabled
    Quickstop Active    Enable Operation   Operation Enable
    Fault               Fault Reset        Switch On Disabled

Enabling the drive from startup:
    Action            Command                             Statusword afterwards
    Shutdown          cansend vcan0 601#2B40600006000000  0x0121
    Switch On         cansend vcan0 601#2B40600007000000  0x0123
    Enable Operation  cansend vcan0 601#2B4060000F000000  0x0537

The last value is Operation Enable (0x0137) plus the target reached bit (0x0400).

Limits:
    Refresh and Measure Init take no time, so Enable Operation goes straight to
    Operation Enable and the Statusword never reports them.
    Nothing causes a fault yet, so Fault and Fault Reaction Active cannot be
    reached. Fault Reset only has an effect in Fault.
    Start and Not Ready to Switch On are skipped: the emulator starts up already
    initialized.
    Quickstop Active only ends on a command, even once the motor has stopped.
    The Controlword bits that no state or mode uses are ignored.

Motion
------
A move follows Profile Position Mode, from the EPOS2 Firmware Specification,
section 5.3. Position is in quadcounts, velocity in rpm and acceleration in rpm/s.
The model in motor.py assumes 2000 quadcounts per revolution (a 500-pulse encoder)
and updates every 10 ms.

To make a move, with the drive in Operation Enable:
    1. Write the position to Target Position (0x607A).
    2. Set the new setpoint bit in Controlword. The drive takes the position and
       sets the setpoint acknowledge bit in Statusword.
    3. Clear the new setpoint bit. The drive clears the acknowledge bit.
    4. Poll Statusword until the target reached bit is set.

Example: enable the drive (see Drive states), then move to position 20000
    Action            Command                             Statusword afterwards
    Write target      cansend vcan0 601#237A6000204E0000  0x0537
    New setpoint      cansend vcan0 601#2B4060001F000000  0x1137
    Clear setpoint    cansend vcan0 601#2B4060000F000000  0x0137
    Read position     cansend vcan0 601#4064600000000000  moving, then 0x0537

These values hold during the move, which takes about 0.7 seconds at the default
profile. Typed by hand, the move may finish first, adding the target reached bit:
0x1537 after the new setpoint, and 0x0537 after clearing it.

Controlword bits, from Table 5-16, page 5-38:
    Bit  Name                    Effect
    4    New setpoint            0 to 1 takes the Target Position
    5    Change set immediately  1 interrupts this move, 0 starts after it
    6    Abs / rel               1 adds the target to the current position
    8    Halt                    1 stops with Profile Deceleration, 0 resumes

Statusword bits, from Table 5-19, page 5-39:
    Bit  Name                 Set when
    10   Target reached       the motor is stopped, with no move left or by Halt
    12   Setpoint acknowledge a setpoint was taken and bit 4 is still set

The ways a move can end early:
    Quickstop               ramps down with Quickstop Deceleration, and drops the move
    Disable Operation,
    Shutdown or
    Disable Voltage         stops at once and drops the move

Limits:
    The position follows the profile exactly, so there is no following error and
    Position Window (0x6067) does not exist yet.
    Moves are always trapezoidal, and Motion Profile Type (0x6086) does not exist
    yet. Neither does Maximal Profile Velocity (0x607F), so nothing limits a
    profile's speed or ramps beyond being above zero.
    Only one move can wait behind the current one, and a newer one replaces it.
    Endless movement (bit 15) is ignored. Changing mode during a move does not
    stop it.
    Setpoints in any other mode are ignored, and those modes do not move the motor.
    Target reached and setpoint acknowledge read as 0 outside Operation Enable and
    Quickstop Active. The spec leaves those bits open in other states.
