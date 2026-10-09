from __future__ import annotations

from abc import ABC, abstractmethod
import json
import os
from pathlib import Path
import struct
import urllib.error
import urllib.request
import zlib

from .contract import DEVICE_ID, HEIGHT_MM, TactileFrame


MAGIC = b"TSE1"
HEADER = struct.Struct(">4sBBBBII")
CRC = struct.Struct(">I")


def pack_two_bit_heights(pins: tuple[int, ...]) -> bytes:
    payload = bytearray((len(pins) + 3) // 4)
    for index, value in enumerate(pins):
        payload[index // 4] |= (value & 0x03) << ((index % 4) * 2)
    return bytes(payload)


def unpack_two_bit_heights(payload: bytes, count: int) -> tuple[int, ...]:
    return tuple(
        (payload[index // 4] >> ((index % 4) * 2)) & 0x03
        for index in range(count)
    )


def encode_wire_frame(frame: TactileFrame) -> bytes:
    payload = pack_two_bit_heights(frame.pins)
    header = HEADER.pack(
        MAGIC,
        frame.version,
        frame.cols,
        frame.rows,
        frame.levels,
        frame.frame_id,
        len(payload),
    )
    body = header + payload
    return body + CRC.pack(zlib.crc32(body) & 0xFFFFFFFF)


def decode_wire_frame(data: bytes) -> TactileFrame:
    if len(data) < HEADER.size + CRC.size:
        raise ValueError("wire frame is shorter than the protocol header")
    magic, version, cols, rows, levels, frame_id, payload_size = HEADER.unpack(data[:HEADER.size])
    if magic != MAGIC:
        raise ValueError("invalid wire frame magic")
    if len(data) != HEADER.size + payload_size + CRC.size:
        raise ValueError("wire frame payload length mismatch")
    expected_crc = CRC.unpack(data[-CRC.size:])[0]
    if zlib.crc32(data[:-CRC.size]) & 0xFFFFFFFF != expected_crc:
        raise ValueError("wire frame CRC32 mismatch")
    pins = unpack_two_bit_heights(data[HEADER.size:-CRC.size], cols * rows)
    return TactileFrame(
        pins=pins,
        frame_id=frame_id,
        cols=cols,
        rows=rows,
        levels=levels,
        version=version,
    )


def hardware_payload(frame: TactileFrame) -> dict:
    return {
        "protocol": "touchsight-matrix-v1",
        "device_id": DEVICE_ID,
        "frame": frame.to_dict(),
        "mechanical_contract": {
            "height_mm": list(HEIGHT_MM),
            "blank_state": "all H0",
            "write_direction": "up",
            "reset": "global release then press to H0",
        },
        "controller_note": "Writer/Lock/Reset timing remains the ESP32 bridge's responsibility.",
    }


class TactileDisplayAdapter(ABC):
    @abstractmethod
    def status(self) -> dict:
        raise NotImplementedError

    @abstractmethod
    def publish(self, frame: TactileFrame) -> dict:
        raise NotImplementedError


class SimulatorAdapter(TactileDisplayAdapter):
    def __init__(self) -> None:
        self.current_frame: TactileFrame | None = None

    def status(self) -> dict:
        return {"name": "simulator", "configured": True, "physical": False}

    def publish(self, frame: TactileFrame) -> dict:
        self.current_frame = frame
        return {"ok": True, "adapter": "simulator", "frame_id": frame.frame_id}


class FileBridgeAdapter(TactileDisplayAdapter):
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir

    def status(self) -> dict:
        return {
            "name": "file-bridge",
            "configured": True,
            "physical": False,
            "output_dir": str(self.output_dir),
        }

    def publish(self, frame: TactileFrame) -> dict:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        json_path = self.output_dir / "current_frame.json"
        wire_path = self.output_dir / "current_frame.tse"
        json_tmp = json_path.with_suffix(".json.tmp")
        wire_tmp = wire_path.with_suffix(".tse.tmp")
        json_tmp.write_text(
            json.dumps(hardware_payload(frame), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        wire_tmp.write_bytes(encode_wire_frame(frame))
        json_tmp.replace(json_path)
        wire_tmp.replace(wire_path)
        return {
            "ok": True,
            "adapter": "file-bridge",
            "physical": False,
            "message": "帧已导出；实体板控制协议尚未冻结，因此没有宣称真机已刷新。",
            "json_path": str(json_path),
            "wire_path": str(wire_path),
            "wire_bytes": wire_path.stat().st_size,
        }


class HttpHardwareAdapter(TactileDisplayAdapter):
    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint.strip()

    def status(self) -> dict:
        return {
            "name": "http-hardware",
            "configured": bool(self.endpoint),
            "physical": True,
            "endpoint": self.endpoint or None,
        }

    def publish(self, frame: TactileFrame) -> dict:
        if not self.endpoint:
            raise RuntimeError("实体板 HTTP 控制地址未配置")
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(hardware_payload(frame), ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                body = response.read(512).decode("utf-8", errors="replace")
                status = getattr(response, "status", 200)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"实体板控制端点发送失败：{exc}") from exc
        return {
            "ok": True,
            "adapter": "http-hardware",
            "physical": True,
            "message": "矩阵已发送到配置的实体板控制桥。",
            "http_status": status,
            "response": body,
        }


class HardwareManager:
    def __init__(self, output_dir: Path) -> None:
        self.file_bridge = FileBridgeAdapter(output_dir)
        endpoint = os.getenv("TOUCHSIGHT_HARDWARE_URL", "").strip()
        self.physical = HttpHardwareAdapter(endpoint) if endpoint else None

    def status(self) -> dict:
        return {
            "file_bridge": self.file_bridge.status(),
            "physical": self.physical.status() if self.physical else {
                "name": "not-configured",
                "configured": False,
                "physical": True,
                "reason": "等待硬件组冻结 ESP32 接收协议或提供 HTTP bridge",
            },
        }

    def publish(self, frame: TactileFrame) -> dict:
        exported = self.file_bridge.publish(frame)
        if self.physical is None:
            return exported
        sent = self.physical.publish(frame)
        return {**exported, **sent, "export": exported}

