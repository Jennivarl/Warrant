"""
Workaround for a genuine Windows-compatibility bug in gltest's direct
execution mode (gltest.direct.loader._inject_message_to_fd0,
genlayer-test 0.29.2): it does tempfile.mkstemp() -> os.dup2(fd, 0) ->
os.unlink(path) while fd 0 still holds the file open via the dup2'd
handle. That's legal on POSIX (files can be unlinked while open) but
raises "[WinError 32] The process cannot access the file because it is
being used by another process" on Windows.

This is not our contract's bug -- it's in third-party site-packages we
don't want to hand-edit (wouldn't survive a reinstall/upgrade). Patched
here instead: same logic, minus the unlink. The temp file is a few bytes
of encoded message data; leaving it behind for the OS to reap later is a
harmless trade-off for being able to run tests on Windows at all.

Unlike glsim's server mode, direct_deploy/direct_vm run everything
in-process within this pytest session, so a conftest.py monkeypatch here
actually takes effect (glsim's server-process mode needed a separate
launcher patch instead -- see the greybox project's history for that
version of this same fix).
"""

import os
import tempfile


def _patch_inject_message_to_fd0_for_windows() -> None:
    try:
        from gltest.direct import loader
    except ImportError:
        return

    def _inject_message_to_fd0_windows_safe(vm) -> None:
        # genlayer-test 0.30 resolves v0.3's genlayer.calldata / genlayer.types
        # through sdk_compat; 0.29 only knew v0.2's genlayer.py layout.
        try:
            from gltest.direct.sdk_compat import import_address, import_calldata

            calldata = import_calldata()
            Address = import_address()
        except ImportError:
            from genlayer.py import calldata
            from genlayer.py.types import Address

        sender_addr = vm.sender
        if isinstance(sender_addr, bytes):
            sender_addr = Address(sender_addr)

        contract_addr = vm._contract_address
        if isinstance(contract_addr, bytes):
            contract_addr = Address(contract_addr)

        origin_addr = vm.origin
        if isinstance(origin_addr, bytes):
            origin_addr = Address(origin_addr)

        message_data = {
            "contract_address": contract_addr,
            "sender_address": sender_addr,
            "origin_address": origin_addr,
            "stack": [],
            "value": vm._value,
            "datetime": vm._datetime,
            "is_init": False,
            "chain_id": vm._chain_id,
            "entry_kind": 0,
            "entry_data": b"",
            "entry_stage_data": None,
        }

        encoded = calldata.encode(message_data)

        fd, path = tempfile.mkstemp()
        os.write(fd, encoded)
        os.lseek(fd, 0, os.SEEK_SET)

        original_stdin = os.dup(0)
        vm._original_stdin_fd = original_stdin

        os.dup2(fd, 0)
        os.close(fd)
        # Intentionally not unlinking `path` -- see module docstring.

    loader._inject_message_to_fd0 = _inject_message_to_fd0_windows_safe


_patch_inject_message_to_fd0_for_windows()
