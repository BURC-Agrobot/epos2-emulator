EPOS2 emulator
==============

Pretends to be a maxon EPOS2 50/5 motor controller on a CAN bus,
so host software can be tested without real hardware.

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
