EPOS2 emulator
==============

Emulates a maxon EPOS2 50/5 motor controller on a CAN bus, so host software can
be tested without hardware. It answers single-frame SDO reads and writes, runs
the drive state machine, and moves a simple motor model in Profile Position Mode.

See startup_sequence.txt for the steps from power on to a finished move.

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

Run
---
    .venv/bin/python emulator.py
    candump vcan0              (in another terminal, to see every frame)

Run only one emulator per bus, or the host gets duplicate replies.

Terminal commands
-----------------
Type these at the >>> prompt. They send nothing on the CAN bus.
    regs           print all registers
    reg 0x6041     print one register (subindex 0x00, with or without the 0x)
    reg 0x6041 -b  also print its value in binary
    help           list the commands
    --help         explain the output (also: emulator.py --help)

Each register prints as:
    0x6041 sub 0x00 RO Statusword = 0x0140

With -b:
    0x6041 sub 0x00 RO Statusword = 0x0140 0b0000_0001_0100_0000

Object dictionary
-----------------
From OBJECTS in object_dictionary.py and the Firmware Specification, chapter 8.
Update this table whenever OBJECTS changes. All are subindex 0x00.

    Index   Name                        Access  Type        Starts at   Unit
    0x1000  Device Type                 RO      UNSIGNED32  0x00020192
    0x1001  Error Register              RO      UNSIGNED8   0x00
    0x6040  Controlword                 RW      UNSIGNED16  0x0000
    0x6041  Statusword                  RO      UNSIGNED16  0x0140
    0x6060  Modes of Operation          RW      INTEGER8    1
    0x6061  Modes of Operation Display  RO      INTEGER8    1
    0x6064  Position Actual Value       RO      INTEGER32   0           quadcounts
    0x606C  Velocity Actual Value       RO      INTEGER32   0           rpm
    0x607A  Target Position             RW      INTEGER32   0           quadcounts
    0x6081  Profile Velocity            RW      UNSIGNED32  1000        rpm
    0x6083  Profile Acceleration        RW      UNSIGNED32  10000       rpm/s
    0x6084  Profile Deceleration        RW      UNSIGNED32  10000       rpm/s
    0x6085  Quickstop Deceleration      RW      UNSIGNED32  10000       rpm/s

Device Type means the CiA 402 drive profile (0x0192) on a servo drive (0x0002).
Modes of Operation accepts any mode in the spec's table. Only 1 moves the motor.
Velocity and ramp registers must be above zero.
The spec gives no start value for 0x6040, 0x6041, 0x6064 or 0x606C.

SDO frames
----------
Each access is one request on CAN ID 601 (0x600 + node ID) and one reply on 581
(0x580 + node ID). Formats are from the Application Notes, section 10.4.

    cansend vcan0 601#<command><index low><index high><subindex><value>

The value is 4 bytes, low byte first, with unused bytes set to 00. For example,
writing 0x0006 to Controlword:

    cansend vcan0 601#2B40600006000000

    2B     write 2 bytes
    40 60  index 0x6040
    00     subindex 0x00
    06 00  value 0x0006
    00 00  unused

Command bytes:
    Byte  Sent by  Meaning
    40    host     read request
    2F    host     write request, 1 byte
    2B    host     write request, 2 bytes
    23    host     write request, 4 bytes
    4F    device   read reply, 1 byte
    4B    device   read reply, 2 bytes
    43    device   read reply, 4 bytes
    60    device   write stored
    80    either   abort, with the code in bytes 4-7

Abort codes:
    Code        Bytes        Meaning
    0x05040001  01 00 04 05  unknown command, or the frame is not 8 bytes
    0x06020000  00 00 02 06  the address does not exist
    0x06010002  02 00 01 06  the register is read-only
    0x06070010  10 00 07 06  the size does not match the register
    0x06090030  30 00 09 06  the value is out of range

A write is checked for address, access, size and then value, in that order.

Run these in order on a freshly started emulator:
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

A host abort (80) and a remote frame (RTR) get no reply.

Limits:
    Multi-frame transfers and command 22 (a write with no stated size) are
    refused as unknown commands. A real EPOS2 accepts them.
    The abort for a frame that is not 8 bytes is the emulator's choice, as the
    Communication Guide does not cover it.
    A wrong subindex gets 0x06020000. A real EPOS2 can send 0x06090011.
    Written values are lost when the emulator stops.

Drive states
------------
The host requests each state change through Controlword, and the drive reports
its state through Statusword. The emulator starts in Switch On Disabled. A command
that does not apply in the current state is stored and acknowledged, but the state
stays the same. The tables are in drive_state.py.

States (Table 3-4, page 3-15), with the spec's "x" bits set to 0:
    State                             Statusword
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

Commands (Table 3-6, page 3-17), where x means the bit is ignored:
    Command            Low byte                Example
    Shutdown           0xxx x110               0x0006
    Switch On          0xxx 0111               0x0007
    Enable Operation   0xxx 1111               0x000F
    Disable Operation  0xxx 0111               0x0007
    Disable Voltage    0xxx xx0x               0x0000
    Quickstop          0xxx x01x               0x0002
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

Limits:
    Start and Not Ready to Switch On are skipped at startup.
    Refresh and Measure Init take no time, so Statusword never reports them.
    Nothing causes a fault yet, so the Fault states cannot be reached.
    Quickstop Active only ends on a command, even once the motor has stopped.

Motion
------
Moves follow Profile Position Mode (Firmware Specification, section 5.3). The
model in motor.py assumes 2000 quadcounts per revolution (a 500-pulse encoder) and
updates every 10 ms.

To make a move in Operation Enable:
    1. Write Target Position (0x607A).
    2. Set Controlword bit 4. The drive sets the acknowledge bit in Statusword.
    3. Clear Controlword bit 4. The drive clears the acknowledge bit.
    4. Poll Statusword until target reached is set.

Controlword bits (Table 5-16, page 5-38):
    Bit  Name                    Effect
    4    New setpoint            0 to 1 takes the Target Position
    5    Change set immediately  1 interrupts this move, 0 starts after it
    6    Abs / rel               1 adds the target to the current position
    8    Halt                    1 stops with Profile Deceleration, 0 resumes

Statusword bits (Table 5-19, page 5-39):
    Bit  Name                  Set when
    10   Target reached        stopped, with no move left or by Halt
    12   Setpoint acknowledge  a setpoint was taken and bit 4 is still set

Both read as 0 outside Operation Enable and Quickstop Active, so an enabled drive
with no move left reads 0x0537 (0x0137 plus bit 10).

Ending a move early:
    Quickstop                                     ramps down with Quickstop Deceleration
    Disable Operation, Shutdown, Disable Voltage  stops at once

Both drop the move.

Limits:
    There is no following error, and no Position Window (0x6067).
    Moves are always trapezoidal, with no Motion Profile Type (0x6086).
    There is no Maximal Profile Velocity (0x607F), so speeds and ramps have no
    upper limit.
    Only one move can wait behind the current one, and a newer one replaces it.
    Endless movement (bit 15) is ignored.
    Changing mode during a move does not stop it.
