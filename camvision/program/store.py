"""Load/save a :class:`~camvision.program.model.Program` as ``.cvprog`` JSON."""

from __future__ import annotations

import json

from .model import Program


def save_program(program: Program, path: str) -> None:
    with open(path, "w") as f:
        json.dump(program.to_dict(), f, indent=2)


def load_program(path: str) -> Program:
    with open(path, "r") as f:
        return Program.from_dict(json.load(f))
